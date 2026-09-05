"""Z-score mean reversion example strategy.

Logic:

- Maintain a rolling window of the last ``window`` prices.
- Compute mean and population std-dev.
- Z-score = (price - mean) / std.
- If z >  entry_z -> SELL (expect reversion to mean).
- If z < -entry_z -> BUY  (expect reversion to mean).
- Exit when |z| < exit_z.

This is intentionally simple. It's the *shape* of a strategy, not the
shape of one you'd put real money behind.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

from bot.orders import OrderSide
from bot.strategies.base import Signal, Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        symbol: str = "DEMO",
        window: int = 20,
        entry_z: float = 1.5,
        exit_z: float = 0.3,
        quantity: float = 10.0,
    ) -> None:
        if window < 2:
            raise ValueError("window must be >= 2")
        self.symbol = symbol
        self.window = window
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.quantity = quantity
        self._prices: deque[float] = deque(maxlen=window)
        self._position: float = 0.0  # + long, - short, 0 flat
        self._last_z: Optional[float] = None

    def reset(self) -> None:
        self._prices.clear()
        self._position = 0.0
        self._last_z = None

    @property
    def last_zscore(self) -> Optional[float]:
        return self._last_z

    def on_prices(self, prices: list[float]) -> list[Signal]:
        if not prices:
            return []
        # Update rolling window with the newest price.
        self._prices.append(prices[-1])

        if len(self._prices) < self.window:
            self._last_z = None
            return []  # not enough history yet

        mean = sum(self._prices) / len(self._prices)
        var = sum((p - mean) ** 2 for p in self._prices) / len(self._prices)
        std = var ** 0.5
        if std == 0:
            self._last_z = 0.0
            return []
        z = (prices[-1] - mean) / std
        self._last_z = z

        signals: list[Signal] = []

        # Exit logic: flatten when we're close to mean again.
        if self._position != 0 and abs(z) < self.exit_z:
            close_side = OrderSide.SELL if self._position > 0 else OrderSide.BUY
            signals.append(
                Signal(
                    symbol=self.symbol,
                    side=close_side,
                    quantity=abs(self._position),
                    reason=f"exit mean reversion (z={z:+.2f})",
                    zscore=z,
                )
            )
            self._position = 0.0
            return signals

        # Entry logic.
        if self._position == 0:
            if z > self.entry_z:
                signals.append(
                    Signal(
                        symbol=self.symbol,
                        side=OrderSide.SELL,
                        quantity=self.quantity,
                        reason=f"overbought (z={z:+.2f})",
                        zscore=z,
                    )
                )
                self._position = -self.quantity
            elif z < -self.entry_z:
                signals.append(
                    Signal(
                        symbol=self.symbol,
                        side=OrderSide.BUY,
                        quantity=self.quantity,
                        reason=f"oversold (z={z:+.2f})",
                        zscore=z,
                    )
                )
                self._position = self.quantity

        return signals


__all__ = ["MeanReversionStrategy"]