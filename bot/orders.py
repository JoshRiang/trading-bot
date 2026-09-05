"""Pydantic models for orders, positions, trades, and quotes.

These are the records that flow through the engine. Keep them immutable-feeling
at the call-site (don't mutate after creation); mutate via a new model copy.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from bot.state import OrderState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Quote(BaseModel):
    """Last-known market quote for a symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    last: float = Field(gt=0)
    timestamp: datetime = Field(default_factory=_utcnow)

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0


class Order(BaseModel):
    """An order ticket tracked end-to-end through OrderState."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    symbol: str
    side: OrderSide
    type: OrderType = OrderType.MARKET
    quantity: float = Field(gt=0)
    limit_price: Optional[float] = Field(default=None, gt=0)
    state: OrderState = OrderState.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: Optional[float] = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    note: Optional[str] = None

    def filled(self) -> bool:
        return self.state == OrderState.FILLED

    def terminal(self) -> bool:
        return self.state in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED)


class Position(BaseModel):
    """A position held in a single symbol."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    quantity: float  # positive=long, negative=short
    avg_price: float = Field(ge=0)

    @property
    def is_flat(self) -> bool:
        return self.quantity == 0


class Trade(BaseModel):
    """A confirmed execution record — emitted when an order fills."""

    model_config = ConfigDict(frozen=True)

    order_id: str
    symbol: str
    side: OrderSide
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    timestamp: datetime = Field(default_factory=_utcnow)