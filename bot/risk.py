"""Risk gate / kill switch.

A single class — :class:`RiskGate` — that the engine consults *before* every
order submission. If any guard fires, the gate refuses the order and reports
the reason. Once tripped, the kill switch stays engaged until manually reset.

Guards:

1. **Max position size** — abs(target_position) <= max_position_size
2. **Max daily loss**   — daily_pnl > -max_daily_loss * capital
3. **Max drawdown**     — current_equity > (1 - max_drawdown) * peak_equity

The gate is *advisory* for live PnL (it doesn't pull quotes itself). The
engine is responsible for feeding in ``daily_pnl`` and ``current_equity`` at
each tick — that's how the bot stays broker-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from bot.orders import Order, OrderSide, Position


class Decision(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class RiskCheck:
    decision: Decision
    reason: str
    guard: Optional[str] = None  # which guard fired, if any


@dataclass
class RiskConfig:
    """Static limits. Anything that's a configuration knob lives here."""

    capital: float = 100_000.0
    max_position_size: float = 100.0           # absolute qty per symbol
    max_daily_loss: float = 0.02               # fraction of capital, e.g. 0.02 = 2%
    max_drawdown: float = 0.10                 # fraction, e.g. 0.10 = 10%
    # If True, hitting any guard trips the kill switch (latched).
    # If False, guards act per-order but trading continues.
    latch_kill_switch: bool = True


@dataclass
class _Runtime:
    peak_equity: float = 0.0
    killed: bool = False
    kill_reason: Optional[str] = None
    kill_at: Optional[datetime] = None


class RiskGate:
    """Pre-trade risk check + kill switch.

    The engine should call :meth:`check` before every order. If ``check``
    returns ``DENY``, do not submit. The engine can also subscribe to
    :attr:`is_killed` for a fast boolean test.

    Example::

        gate = RiskGate(RiskConfig(capital=100_000))
        result = gate.check(order, positions={"AAPL": pos}, daily_pnl=-100, equity=99_900)
        if result.decision == Decision.DENY:
            log.warning("risk denied", reason=result.reason)
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config: RiskConfig = config or RiskConfig()
        self._rt = _Runtime(peak_equity=self.config.capital)

    # -- public state ----------------------------------------------------

    @property
    def is_killed(self) -> bool:
        return self._rt.killed

    @property
    def kill_reason(self) -> Optional[str]:
        return self._rt.kill_reason

    @property
    def peak_equity(self) -> float:
        return self._rt.peak_equity

    @property
    def current_drawdown(self) -> float:
        """Return drawdown as a positive fraction, e.g. 0.04 = 4% drawdown."""
        peak = self._rt.peak_equity
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - self._last_equity) / peak)

    # keep last equity so current_drawdown() is meaningful
    _last_equity: float = 0.0

    # -- main entry -------------------------------------------------------

    def check(
        self,
        order: Order,
        positions: dict[str, Position],
        daily_pnl: float,
        equity: float,
    ) -> RiskCheck:
        """Evaluate an order against all guards.

        Returns a :class:`RiskCheck`. If ``latch_kill_switch`` is True and any
        guard fires, the gate is latched until :meth:`reset` is called.
        """

        # Track peak equity / current drawdown on every call.
        self._last_equity = equity
        if equity > self._rt.peak_equity:
            self._rt.peak_equity = equity

        # 1) Latch: if already killed, refuse everything.
        if self._rt.killed:
            return RiskCheck(
                Decision.DENY,
                reason=f"kill switch engaged: {self._rt.kill_reason}",
                guard="kill_switch",
            )

        # 2) Daily loss guard.
        loss_limit = -self.config.max_daily_loss * self.config.capital
        if daily_pnl < loss_limit:
            return self._trip(
                guard="daily_loss",
                reason=(
                    f"daily_pnl={daily_pnl:.2f} below limit "
                    f"{loss_limit:.2f} (max_daily_loss={self.config.max_daily_loss:.2%})"
                ),
            )

        # 3) Drawdown guard.
        if self._rt.peak_equity > 0:
            dd = (self._rt.peak_equity - equity) / self._rt.peak_equity
            if dd > self.config.max_drawdown:
                return self._trip(
                    guard="drawdown",
                    reason=(
                        f"drawdown={dd:.2%} exceeds limit "
                        f"{self.config.max_drawdown:.2%} (peak={self._rt.peak_equity:.2f})"
                    ),
                )

        # 4) Max position size guard — projected post-trade position.
        current = positions.get(order.symbol, Position(symbol=order.symbol, quantity=0.0, avg_price=0.0))
        signed = order.quantity if order.side == OrderSide.BUY else -order.quantity
        projected = current.quantity + signed
        if abs(projected) > self.config.max_position_size:
            return RiskCheck(
                Decision.DENY,
                reason=(
                    f"projected position {projected:+.0f} exceeds "
                    f"max_position_size={self.config.max_position_size:.0f} for {order.symbol}"
                ),
                guard="position_size",
            )

        return RiskCheck(Decision.ALLOW, reason="ok")

    # -- controls ---------------------------------------------------------

    def reset(self) -> None:
        """Manually un-trip the kill switch."""
        self._rt.killed = False
        self._rt.kill_reason = None
        self._rt.kill_at = None
        # Don't reset peak_equity — drawdown is measured from session peak.

    def force_kill(self, reason: str) -> None:
        """External trip (e.g. operator panic button)."""
        self._trip(guard="manual", reason=reason)

    # -- internal ---------------------------------------------------------

    def _trip(self, guard: str, reason: str) -> RiskCheck:
        if self.config.latch_kill_switch:
            self._rt.killed = True
            self._rt.kill_reason = f"{guard}: {reason}"
            self._rt.kill_at = datetime.now(timezone.utc)
        return RiskCheck(Decision.DENY, reason=reason, guard=guard)


__all__ = ["RiskGate", "RiskConfig", "RiskCheck", "Decision"]