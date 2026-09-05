# Trading Bot Skeleton

A broker-agnostic trading bot skeleton in Python. Plug in any broker by implementing
the `Broker` ABC, swap in any strategy, and let the engine route signals through the
risk kill-switch before they ever reach an order ticket.

Designed for **safety first**:

- A `KillSwitch` halts trading on max position size, max daily loss, or max drawdown.
- All order state transitions flow through an explicit state machine.
- Every state change and order event is emitted as structured JSON via `structlog`.
- A `PaperBroker` ships out of the box so strategies can be exercised end-to-end
  without touching real money.

## Architecture

```
strategy.signal() -> engine.on_signal()
        |
        v
  RiskGate.check(signal)   <-- KillSwitch lives here
        |
        v
  Broker.submit_order()
        |
        v
  OrderState machine: PENDING -> SUBMITTED -> FILLED / CANCELLED / REJECTED
```

## Layout

```
bot/
  __init__.py
  __main__.py            CLI entry: python -m bot --strategy mean_reversion --broker paper
  engine.py              Main loop: signal -> risk -> submit -> log
  orders.py              Pydantic models: Order, Position, Trade
  state.py               OrderState enum and legal-transition table
  risk.py                KillSwitch: position size, daily loss, drawdown
  logging_config.py      structlog JSON setup
  brokers/
    base.py              Broker ABC
    paper.py             In-memory broker, instant fills at last quote
  strategies/
    mean_reversion.py    Z-score mean reversion example
tests/
  test_state.py
  test_risk.py
  test_paper_broker.py
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
# Smoke run with the in-memory paper broker
python -m bot --strategy mean_reversion --broker paper --iterations 5
```

CLI flags:

- `--strategy`  strategy name (default: `mean_reversion`)
- `--broker`    broker name (default: `paper`)
- `--iterations`  how many synthetic ticks to run (default: `3`)
- `--capital`    starting capital for the risk gate (default: `100000`)
- `--max-position`  max absolute position per symbol (default: `100`)
- `--max-daily-loss`  fraction of capital, e.g. `0.02` (default: `0.02`)
- `--max-drawdown`    fraction, e.g. `0.10` (default: `0.10`)

## Test

```bash
pytest -q
```

## Adding a broker

Implement the `Broker` ABC in `bot/brokers/your_broker.py`:

```python
from bot.brokers.base import Broker

class MyBroker(Broker):
    def connect(self) -> None: ...
    def get_positions(self) -> dict[str, Position]: ...
    def submit_order(self, order: Order) -> Order: ...
    def cancel_order(self, order_id: str) -> Order: ...
    def get_quote(self, symbol: str) -> Quote: ...
```

Register it in `bot/__main__.py` (or your own launcher) and you're done.

## License

MIT