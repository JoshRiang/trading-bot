"""Broker abstract base class.

Every concrete broker (paper, interactive-brokers, alpaca, ...) implements
this ABC.  The engine talks to brokers through this interface only, so
swapping execution venues does not touch strategy or risk code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from bot.orders import Order, Position, Quote


class BrokerError(Exception):
    """Base class for broker errors the engine is expected to handle."""


class BrokerConnectionError(BrokerError):
    """Raised when connect() fails."""


class BrokerOrderRejected(BrokerError):
    """Raised (or returned as a REJECTED order) when the broker refuses."""

    def __init__(self, message: str, order_id: Optional[str] = None):
        self.order_id = order_id
        super().__init__(message)


class Broker(ABC):
    """A broker that the engine can submit orders to."""

    name: str = "abstract"

    @abstractmethod
    def connect(self) -> None:
        """Open any session / authenticate. Must be idempotent."""

    @abstractmethod
    def disconnect(self) -> None:
        """Cleanly close the session. Must be idempotent."""

    @abstractmethod
    def get_positions(self) -> dict[str, Position]:
        """Return current positions keyed by symbol."""

    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Return the latest quote for ``symbol``."""

    @abstractmethod
    def submit_order(self, order: Order) -> Order:
        """Submit an order and return it with state mutated to SUBMITTED or FILLED.

        Implementations should mutate ``order.state`` in place and return the
        same object. For the paper broker this is also where the fill happens.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> Order:
        """Cancel an open order by id and return it with state CANCELLED."""

    def get_position(self, symbol: str) -> Position:
        """Convenience: single-symbol position lookup (default uses get_positions)."""
        return self.get_positions().get(
            symbol, Position(symbol=symbol, quantity=0.0, avg_price=0.0)
        )