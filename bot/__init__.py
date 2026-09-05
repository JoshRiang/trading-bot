"""Trading bot skeleton.

A broker-agnostic, strategy-pluggable trading bot with an explicit order state
machine, a kill-switch risk gate, and structured JSON logging.
"""

from bot.orders import Order, OrderSide, OrderType, Position, Quote, Trade
from bot.state import OrderState, is_legal_transition

__all__ = [
    "Order",
    "OrderSide",
    "OrderType",
    "Position",
    "Quote",
    "Trade",
    "OrderState",
    "is_legal_transition",
]

__version__ = "0.1.0"