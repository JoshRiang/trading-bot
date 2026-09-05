"""Strategy base class.

A strategy receives a sequence of prices (and the broker, so it can look up
its current positions) and emits zero or more :class:`Signal` objects per
tick. The engine is responsible for converting signals into orders and
running them through the risk gate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from bot.orders import OrderSide


@dataclass(frozen=True)
class Signal:
    """A trading signal emitted by a strategy."""

    symbol: str
    side: OrderSide
    quantity: float
    reason: str = ""
    zscore: Optional[float] = None  # populated by mean reversion


class Strategy(ABC):
    """Base class for strategies."""

    name: str = "abstract"

    @abstractmethod
    def on_prices(self, prices: list[float]) -> list[Signal]:
        """Called once per tick with the recent price window for the symbol.

        ``prices`` is in chronological order, last element is the most recent.
        Strategies that need multi-symbol data should be called separately
        per symbol, or take an explicit ``MarketState`` (future work).
        """

    def reset(self) -> None:
        """Optional: clear any internal state."""
        return None