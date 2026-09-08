"""CLI entry point.

    python -m bot --strategy mean_reversion --broker paper --iterations 5

Wires a strategy + paper broker + risk gate together and runs a few synthetic
ticks so you can see structured logs and confirm the wiring.
"""

# Maintenance: last reviewed 2026-09-08 (daily improvement cycle)

from __future__ import annotations

import argparse
import random
import sys

from bot.brokers.base import Broker
from bot.brokers.paper import PaperBroker
from bot.engine import Engine
from bot.logging_config import configure_logging, get_logger
from bot.orders import Quote
from bot.risk import RiskConfig, RiskGate
from bot.strategies.mean_reversion import MeanReversionStrategy


def _build_broker(name: str) -> Broker:
    if name == "paper":
        return PaperBroker()
    raise SystemExit(f"unknown broker: {name!r} (only 'paper' ships with this skeleton)")


def _build_strategy(name: str):
    if name == "mean_reversion":
        return MeanReversionStrategy(symbol="DEMO", window=10, entry_z=1.0, exit_z=0.2, quantity=10)
    raise SystemExit(f"unknown strategy: {name!r} (only 'mean_reversion' ships with this skeleton)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bot", description="Trading bot skeleton")
    parser.add_argument("--strategy", default="mean_reversion", help="strategy name")
    parser.add_argument("--broker", default="paper", help="broker name")
    parser.add_argument("--iterations", type=int, default=3, help="synthetic ticks to run")
    parser.add_argument("--capital", type=float, default=100_000.0)
    parser.add_argument("--max-position", type=float, default=100.0)
    parser.add_argument("--max-daily-loss", type=float, default=0.02)
    parser.add_argument("--max-drawdown", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-json", action="store_true", help="render logs as text")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    configure_logging(level=args.log_level, json_output=not args.no_json)
    log = get_logger("bot.cli")

    log.info(
        "starting",
        strategy=args.strategy,
        broker=args.broker,
        iterations=args.iterations,
        capital=args.capital,
    )

    broker = _build_broker(args.broker)
    broker.connect()
    if hasattr(broker, "set_cash"):
        broker.set_cash(args.capital)

    # Seed a quote so the broker has something to fill against.
    broker.set_quote(Quote(symbol="DEMO", bid=100.0, ask=100.0, last=100.0))

    risk = RiskGate(
        RiskConfig(
            capital=args.capital,
            max_position_size=args.max_position,
            max_daily_loss=args.max_daily_loss,
            max_drawdown=args.max_drawdown,
        )
    )

    strategy = _build_strategy(args.strategy)
    engine = Engine(strategy=strategy, broker=broker, risk_gate=risk)
    engine.state.cash = args.capital
    engine.state.equity = args.capital

    rng = random.Random(args.seed)
    price = 100.0
    for i in range(args.iterations):
        price = max(1.0, price + rng.gauss(0.0, 2.0))
        log.info("tick", iteration=i, symbol="DEMO", price=round(price, 4))
        engine.run_once("DEMO", price)
        if engine.risk.is_killed:
            log.warning("halted_by_kill_switch", reason=engine.risk.kill_reason)
            break

    log.info(
        "summary",
        fills=engine.state.fills,
        denied=engine.state.denied,
        rejects=engine.state.rejects,
        killed=engine.risk.is_killed,
    )

    broker.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())