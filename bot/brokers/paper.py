"""In-memory paper broker.

Tracks positions in memory and fills market orders instantly at the last quote.
No slippage, no latency. Use it for unit tests and strategy dry-runs.

Lifecycle of an order through this broker:

    PENDING -> SUBMITTED -> FILLED     (market order, default)
    PENDING -> SUBMITTED               (limit order waiting for price)
    PENDING -> REJECTED                (unknown symbol / no quote)

The broker keeps a tiny state machine of its own so the limit-order case can
be exercised manually via ``advance()`` for tests that care about partial
fills.
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional

from bot.brokers.base import Broker, BrokerError, BrokerOrderRejected
from bot.orders import Order, OrderState, OrderType, Position, Quote, Trade
from bot.state import assert_legal_transition


class PaperBroker(Broker):
    """In-memory broker; fills at last quote, no slippage."""

    name = "paper"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._quotes: dict[str, Quote] = {}
        self._positions: dict[str, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._cash: float = 0.0
        self._connected: bool = False

    # -- lifecycle --------------------------------------------------------

    def connect(self) -> None:
        with self._lock:
            self._connected = True

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False

    def set_cash(self, cash: float) -> None:
        """Initialise paper cash (e.g. when seeding a backtest)."""
        with self._lock:
            self._cash = float(cash)

    def is_connected(self) -> bool:
        return self._connected

    # -- market data ------------------------------------------------------

    def set_quote(self, quote: Quote) -> None:
        """Inject a quote. In live usage, this would come from a feed."""
        with self._lock:
            self._quotes[quote.symbol] = quote

    def get_quote(self, symbol: str) -> Quote:
        with self._lock:
            q = self._quotes.get(symbol)
        if q is None:
            raise BrokerError(f"no quote for {symbol}")
        return q

    # -- positions --------------------------------------------------------

    def get_positions(self) -> dict[str, Position]:
        with self._lock:
            return dict(self._positions)

    # -- orders -----------------------------------------------------------

    def submit_order(self, order: Order) -> Order:
        with self._lock:
            if not self._connected:
                raise BrokerError("broker not connected")
            if order.id in self._orders:
                raise BrokerError(f"duplicate order id {order.id}")
            try:
                quote = self._quotes[order.symbol]
            except KeyError as exc:
                # No quote -> rejection. Flip state through the legal path.
                assert_legal_transition(order.state, OrderState.REJECTED)
                order.state = OrderState.REJECTED
                order.note = f"no quote for {order.symbol}"
                self._orders[order.id] = order
                raise BrokerOrderRejected(order.note, order_id=order.id) from exc

            # Move PENDING -> SUBMITTED.
            assert_legal_transition(order.state, OrderState.SUBMITTED)
            order.state = OrderState.SUBMITTED

            if order.type == OrderType.MARKET:
                fill_price = quote.last
                self._fill(order, fill_price, order.quantity)
            else:
                # LIMIT orders are kept in SUBMITTED until advance() or cancel().
                pass

            self._orders[order.id] = order
            return order

    def cancel_order(self, order_id: str) -> Order:
        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                raise BrokerError(f"unknown order {order_id}")
            if order.terminal():
                return order  # idempotent: already terminal
            assert_legal_transition(order.state, OrderState.CANCELLED)
            order.state = OrderState.CANCELLED
            return order

    def advance(self, quote: Quote) -> list[Trade]:
        """Push a new quote and let any matching LIMIT orders fill.

        Useful for tests that want to simulate price movement and partial fills
        without writing a full matching engine.
        """
        with self._lock:
            self._quotes[quote.symbol] = quote
            filled: list[Trade] = []
            for order in list(self._orders.values()):
                if order.state != OrderState.SUBMITTED:
                    continue
                if order.symbol != quote.symbol:
                    continue
                if order.type != OrderType.LIMIT or order.limit_price is None:
                    continue
                if order.side.value == "BUY" and quote.last <= order.limit_price:
                    self._fill(order, order.limit_price, order.quantity)
                    filled.append(self._trades[-1])
                elif order.side.value == "SELL" and quote.last >= order.limit_price:
                    self._fill(order, order.limit_price, order.quantity)
                    filled.append(self._trades[-1])
            return filled

    # -- introspection (for tests / engine) --------------------------------

    def list_orders(self) -> list[Order]:
        with self._lock:
            return list(self._orders.values())

    def list_trades(self) -> list[Trade]:
        with self._lock:
            return list(self._trades)

    def cash(self) -> float:
        with self._lock:
            return self._cash

    # -- internal ---------------------------------------------------------

    def _fill(self, order: Order, price: float, qty: float) -> None:
        """Mutate order to FILLED and update position / cash. Caller holds the lock."""
        assert_legal_transition(order.state, OrderState.FILLED)
        order.state = OrderState.FILLED
        order.filled_quantity = qty
        order.avg_fill_price = price

        # Update position.
        existing = self._positions.get(order.symbol)
        signed_qty = qty if order.side.value == "BUY" else -qty
        if existing is None:
            new_qty = signed_qty
            new_avg = price
        else:
            new_qty = existing.quantity + signed_qty
            if new_qty == 0:
                new_avg = 0.0
            elif (existing.quantity >= 0 and signed_qty >= 0) or (
                existing.quantity <= 0 and signed_qty <= 0
            ):
                # Same-side add: weighted average.
                total_cost = existing.avg_price * abs(existing.quantity) + price * qty
                new_avg = total_cost / abs(new_qty) if new_qty != 0 else 0.0
            else:
                # Crossing zero or reducing: keep prior avg until flat.
                new_avg = existing.avg_price

        if new_qty == 0:
            self._positions.pop(order.symbol, None)
        else:
            self._positions[order.symbol] = Position(
                symbol=order.symbol, quantity=new_qty, avg_price=new_avg
            )

        # Cash accounting.
        self._cash -= price * signed_qty

        self._trades.append(
            Trade(
                order_id=order.id,
                symbol=order.symbol,
                side=order.side,
                quantity=qty,
                price=price,
            )
        )


def make_order_id(prefix: str = "ord") -> str:
    """Convenience: generate a short, unique-ish order id."""
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


__all__ = ["PaperBroker", "make_order_id"]


def _optional_imports() -> None:  # pragma: no cover - type hint aid
    from bot.orders import OrderSide  # noqa: F401