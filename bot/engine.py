"""Trading engine.

The engine wires together: a strategy, a risk gate, a broker, and a price
source. Each tick:

    1. Read latest price(s).
    2. Ask the strategy for signals.
    3. For each signal, build an order, run it through the risk gate.
    4. If approved, submit to the broker. If denied, log the reason.
    5. Update internal PnL accounting.

The engine itself doesn't know how quotes arrive — that's the price source's
job. In a backtest you'd subclass :class:`Engine` and override
:meth:`next_price`. In production you'd feed via a websocket callback.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Callable, Optional

from bot.brokers.base import Broker, BrokerOrderRejected
from bot.brokers.paper import make_order_id
from bot.logging_config import get_logger
from bot.orders import Order, OrderSide, OrderType, Position
from bot.risk import Decision, RiskConfig, RiskGate
from bot.strategies.base import Signal, Strategy

log = get_logger(__name__)


PriceSource = Callable[[str], float]


@dataclass
class EngineState:
    """Mutable bookkeeping kept by the engine across ticks."""

    cash: float = 0.0
    daily_pnl: float = 0.0
    equity: float = 0.0
    peak_equity: float = 0.0
    fills: int = 0
    rejects: int = 0
    denied: int = 0
    order_seq: itertools.count = field(default_factory=lambda: itertools.count(1))


class Engine:
    """Wires strategy -> risk -> broker, runs the main loop.

    Designed to be driven either by an event loop (``run_once`` per tick) or
    in a batched dry-run (``run``).
    """

    def __init__(
        self,
        strategy: Strategy,
        broker: Broker,
        risk_gate: RiskGate,
        price_source: Optional[PriceSource] = None,
    ) -> None:
        self.strategy = strategy
        self.broker = broker
        self.risk = risk_gate
        self.price_source = price_source or (lambda symbol: 100.0)
        self.state = EngineState(cash=0.0, equity=risk_gate.config.capital)
        self.state.peak_equity = risk_gate.config.capital

    # -- main loop --------------------------------------------------------

    def run(self, prices_by_symbol: dict[str, list[float]]) -> None:
        """Drive the engine once per symbol over a list of price histories.

        For tests and smoke runs. Production code typically calls run_once in
        a loop driven by an event source.
        """
        for symbol, history in prices_by_symbol.items():
            for price in history:
                self._update_quote(symbol, price)
                signals = self.strategy.on_prices([price])
                for sig in signals:
                    self.on_signal(sig)

    def run_once(self, symbol: str, price: float) -> list[Order]:
        """Single tick: update quote, run strategy, dispatch signals."""
        self._update_quote(symbol, price)
        signals = self.strategy.on_prices([price])
        orders: list[Order] = []
        for sig in signals:
            order = self.on_signal(sig)
            if order is not None:
                orders.append(order)
        return orders

    # -- signal handler ---------------------------------------------------

    def on_signal(self, signal: Signal) -> Optional[Order]:
        """Build an order from a signal, run the risk gate, submit.

        Returns the submitted (or rejected) :class:`Order`, or ``None`` if
        the risk gate refused the order before it ever became a ticket.
        """
        log.info(
            "signal_received",
            strategy=self.strategy.name,
            symbol=signal.symbol,
            side=signal.side.value,
            quantity=signal.quantity,
            reason=signal.reason,
            zscore=signal.zscore,
        )

        order = self._build_order(signal)

        result = self.risk.check(
            order,
            positions=self.broker.get_positions(),
            daily_pnl=self.state.daily_pnl,
            equity=self.state.equity,
        )

        if result.decision == Decision.DENY:
            self.state.denied += 1
            log.warning(
                "risk_denied",
                order_id=order.id,
                guard=result.guard,
                reason=result.reason,
            )
            if result.guard == "kill_switch" or self.risk.is_killed:
                log.error(
                    "kill_switch_engaged",
                    reason=self.risk.kill_reason,
                )
            return None

        # Risk approved. Hand to broker.
        try:
            submitted = self.broker.submit_order(order)
        except BrokerOrderRejected as exc:
            self.state.rejects += 1
            log.error(
                "order_rejected",
                order_id=order.id,
                reason=str(exc),
            )
            return order

        if submitted.filled():
            self.state.fills += 1
            self._mark_to_market()
            log.info(
                "order_filled",
                order_id=submitted.id,
                symbol=submitted.symbol,
                side=submitted.side.value,
                quantity=submitted.filled_quantity,
                price=submitted.avg_fill_price,
            )
        else:
            log.info(
                "order_submitted",
                order_id=submitted.id,
                symbol=submitted.symbol,
                side=submitted.side.value,
                state=submitted.state.value,
            )

        return submitted

    # -- helpers ----------------------------------------------------------

    def _build_order(self, signal: Signal) -> Order:
        order_id = make_order_id(prefix=self.strategy.name)
        return Order(
            id=order_id,
            symbol=signal.symbol,
            side=signal.side,
            type=OrderType.MARKET,
            quantity=signal.quantity,
        )

    def _update_quote(self, symbol: str, price: float) -> None:
        # Best-effort: only the PaperBroker supports set_quote via this path,
        # but the engine stays generic — if the broker doesn't have set_quote,
        # we just skip and rely on the broker's own feed.
        setter = getattr(self.broker, "set_quote", None)
        if callable(setter):
            from bot.orders import Quote

            setter(Quote(symbol=symbol, bid=price, ask=price, last=price))

    def _mark_to_market(self) -> None:
        """Refresh equity from cash + positions marked at last quote."""
        equity = self.broker.cash() if hasattr(self.broker, "cash") else self.state.cash
        for pos in self.broker.get_positions().values():
            try:
                q = self.broker.get_quote(pos.symbol)
                equity += pos.quantity * q.last
            except Exception:
                # No quote for this symbol — assume zero mark.
                pass
        # Track daily PnL against peak (rough).
        prev = self.state.equity or self.state.peak_equity
        self.state.daily_pnl += equity - prev
        self.state.equity = equity
        if equity > self.state.peak_equity:
            self.state.peak_equity = equity


__all__ = ["Engine", "EngineState"]