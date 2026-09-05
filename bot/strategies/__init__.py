"""Strategy sub-package.

A strategy turns market data into :class:`~bot.orders.Order` objects (or, more
specifically, into trade *signals*).  The engine translates signals into
orders after the risk gate approves.
"""

from bot.strategies.base import Signal, Strategy

__all__ = ["Signal", "Strategy"]