# trading-bot

A broker-agnostic trading engine with an explicit order state machine, a latching
kill switch, and structured JSON logging. It ships with an in-memory paper broker
and one example strategy, so the whole path — signal → risk gate → order → fill →
log — runs on synthetic data with no broker account.

This is a skeleton, deliberately. The point is the wiring: where a strategy plugs
in, where risk is enforced before submission, and how an order's lifecycle is
made legal or illegal in one place. The example strategy is the shape of a
strategy, not the shape of one you would put money behind.

**Stack:** Python 3.10+ · pydantic · structlog · pytest

---

## Install

```bash
git clone https://github.com/JoshRiang/trading-bot.git
cd trading-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Run it

The CLI drives a few synthetic ticks through the full pipeline and prints
structured logs so you can confirm the wiring:

```bash
python -m bot --strategy mean_reversion --broker paper --iterations 6 --seed 42 --no-json
```

```
2026-11-07T05:56:27Z [info ] starting   [PrintLogger] broker=paper capital=100000.0 iterations=6 strategy=mean_reversion
2026-11-07T05:56:27Z [info ] tick       [PrintLogger] iteration=0 price=99.7118 symbol=DEMO
2026-11-07T05:56:27Z [info ] tick       [PrintLogger] iteration=1 price=99.366 symbol=DEMO
...
2026-11-07T05:56:27Z [info ] summary    [PrintLogger] denied=0 fills=0 killed=False rejects=0
```

Six ticks against a `window=10` strategy never fills the rolling window, so no
signal fires and the summary reads `fills=0`. Raise `--iterations` past the
window and the z-score entries begin to appear.

Flags: `--strategy`, `--broker`, `--iterations`, `--capital`, `--max-position`,
`--max-daily-loss`, `--max-drawdown`, `--seed`, `--no-json`, `--log-level`. Only
`paper` and `mean_reversion` ship in the skeleton; anything else exits with a
named error.

## Use it in code

```python
from bot.brokers.paper import PaperBroker
from bot.engine import Engine
from bot.logging_config import configure_logging
from bot.orders import Quote
from bot.risk import RiskConfig, RiskGate
from bot.strategies.mean_reversion import MeanReversionStrategy

configure_logging(level="INFO", json_output=True)

broker = PaperBroker()
broker.connect()
broker.set_cash(100_000.0)
broker.set_quote(Quote(symbol="DEMO", bid=100.0, ask=100.0, last=100.0))

risk = RiskGate(RiskConfig(
    capital=100_000.0,
    max_position_size=100.0,   # absolute qty per symbol
    max_daily_loss=0.02,       # 2% of capital
    max_drawdown=0.10,         # 10% from peak
))

strategy = MeanReversionStrategy(symbol="DEMO", window=10, entry_z=1.0, exit_z=0.2, quantity=10)
engine = Engine(strategy=strategy, broker=broker, risk_gate=risk)
engine.state.cash = 100_000.0
engine.state.equity = 100_000.0

engine.run_once("DEMO", 101.5)   # one tick: quote → strategy → risk → broker
```

`run_once` is the unit of work. A backtest loops it over a price history (the
batched `Engine.run` does exactly that); a live system calls it from a feed
callback. The engine never pulls quotes itself, so it stays venue-neutral.

---

## How it fits together

```
bot/
  __init__.py          public exports: Order, OrderSide, OrderType, Position, Quote, Trade, OrderState, is_legal_transition
  __main__.py          CLI: wires strategy + paper broker + risk gate, drives synthetic ticks
  engine.py            Engine + EngineState: signal → risk check → submit → mark-to-market
  state.py             OrderState machine and the legal-transition graph
  orders.py            pydantic models: Quote, Order, Position, Trade (+ OrderSide/OrderType)
  risk.py              RiskGate, RiskConfig, RiskCheck, Decision
  logging_config.py    structlog setup (JSON lines on stdout, stdlib bridge)
  brokers/
    base.py            Broker ABC + BrokerError / BrokerConnectionError / BrokerOrderRejected
    paper.py           PaperBroker: in-memory fills at last quote, no slippage
  strategies/
    base.py            Strategy ABC + Signal dataclass
    mean_reversion.py  z-score example strategy
tests/
  test_state.py  test_risk.py  test_paper_broker.py
.github/workflows/ci.yml   pytest + flake8 on Python 3.10 / 3.11 / 3.12
```

Four seams, each an ABC or a frozen model boundary, so swapping one layer does not
touch the others:

**Strategy → Signal.** A strategy gets a list of recent prices and returns zero or
more `Signal`s (`bot/strategies/base.py`). It does not build orders and does not
see the broker. `MeanReversionStrategy` keeps a rolling window, computes a
population z-score, sells when `z > entry_z`, buys when `z < -entry_z`, and flattens
when `|z| < exit_z`.

**Signal → Order → RiskGate.** The engine turns a signal into a market `Order`
and asks the gate before anything reaches the broker (`bot/risk.py`). Three guards:

| guard | condition | behaviour |
|---|---|---|
| `position_size` | `abs(projected) > max_position_size` | denies this order only (never latches) |
| `daily_loss` | `daily_pnl < -max_daily_loss × capital` | trips the kill switch |
| `drawdown` | `(peak − equity)/peak > max_drawdown` | trips the kill switch |

When `latch_kill_switch` is true (the default), a tripped guard engages the switch
and every later order is denied with `guard="kill_switch"` until `reset()` is
called. `force_kill(reason)` is the manual panic button. The gate is advisory on
PnL: the engine feeds it `daily_pnl` and `equity` each tick, which is how the bot
stays broker-agnostic.

**Order → Broker.** The `Broker` ABC (`bot/brokers/base.py`) is `connect`,
`disconnect`, `get_positions`, `get_quote`, `submit_order`, `cancel_order`.
`PaperBroker` fills market orders instantly at the last quote, holds limit orders
in `SUBMITTED` until price arrives, rejects unknown symbols and duplicate ids, and
tracks cash, positions and a trade tape in memory under a lock.

**Order lifecycle.** `bot/state.py` is the single source of truth for legal moves:

```
PENDING ──► SUBMITTED ──► FILLED
   │            │        └─► CANCELLED / REJECTED
   ├─► CANCELLED / REJECTED
   └─(no direct PENDING ► FILLED)
```

`FILLED`, `CANCELLED` and `REJECTED` are absorbing; no-op transitions are illegal;
anything outside the graph raises `IllegalTransitionError`. The paper broker
asserts every transition through this graph before mutating an order, so an
impossible state change fails loudly instead of corrupting the book.

**Logging.** `configure_logging()` routes structlog and the stdlib root logger
through one pipeline; every event is a JSON line with timestamp, level, logger and
the structured kwargs from the call site (`engine.py` emits `signal_received`,
`risk_denied`, `order_filled`, `order_rejected`; `__main__.py` emits `starting`,
`tick`, `summary`). `--no-json` switches to a plain console renderer.

---

## Tests

```bash
pytest -v
```

50 tests, all deterministic and dependency-free (synthetic prices, in-memory
broker):

```
tests/test_paper_broker.py .................   [34%]  fills, shorts, flat-netting, limit holds, cancels, rejections, cash
tests/test_risk.py             ...............   [64%]  allow path, position-size overflow (buy + short), exit-toward-flat, non-latched mode, daily-loss kill, drawdown kill, latch/reset/force-kill
tests/test_state.py            ..................  [100%]  legal/illegal transition pairs, terminal absorbing, no-op illegal, happy path, rejection path
50 passed
```

CI runs the same suite plus a flake8 syntax/undefined-name gate on Python 3.10,
3.11 and 3.12 (`.github/workflows/ci.yml`).

---

## Extending

Add a strategy by subclassing `Strategy` and returning `Signal`s from
`on_prices`; register it in `_build_strategy` in `bot/__main__.py`. Add a venue by
implementing the `Broker` ABC; the engine and risk gate do not change. Keep the
risk gate as the only place a limit is enforced — the engine consults it before
every submission, so a new guard belongs in `RiskGate.check`, not in a strategy.

## Roadmap

- [x] Order state machine with a legal-transition graph
- [x] Risk gate with latching kill switch (position size, daily loss, drawdown)
- [x] In-memory paper broker with market + limit fills
- [x] Example z-score mean-reversion strategy
- [x] Structured JSON logging
- [ ] A live broker adapter behind the same `Broker` ABC
- [ ] More strategy implementations
- [ ] Partial-fill and slippage models in the paper broker

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Open an issue before substantive changes;
add tests for new behaviour; keep public functions type-hinted.

## License

MIT — see [LICENSE](LICENSE).

## Author

**Joshua Riangkamang** — [github.com/JoshRiang](https://github.com/JoshRiang)
