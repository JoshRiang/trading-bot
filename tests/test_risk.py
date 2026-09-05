"""Tests for the RiskGate / kill switch."""

from __future__ import annotations

import pytest

from bot.orders import Order, OrderSide, OrderType, Position
from bot.risk import Decision, RiskConfig, RiskGate


def _buy(symbol: str = "DEMO", qty: float = 10.0) -> Order:
    return Order(
        id="test-1",
        symbol=symbol,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=qty,
    )


def _sell(symbol: str = "DEMO", qty: float = 10.0) -> Order:
    return Order(
        id="test-1",
        symbol=symbol,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=qty,
    )


def _positions(**sym_qty) -> dict[str, Position]:
    return {
        sym: Position(symbol=sym, quantity=qty, avg_price=100.0)
        for sym, qty in sym_qty.items()
        if qty != 0
    }


# --- happy path ----------------------------------------------------------


def test_allows_normal_order() -> None:
    gate = RiskGate(RiskConfig(capital=100_000, max_position_size=100))
    result = gate.check(_buy(qty=10), _positions(), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.ALLOW
    assert result.reason == "ok"
    assert gate.is_killed is False


# --- position size guard --------------------------------------------------


def test_position_size_blocks_overflow_buy() -> None:
    gate = RiskGate(RiskConfig(capital=100_000, max_position_size=50))
    result = gate.check(_buy(qty=80), _positions(), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.DENY
    assert result.guard == "position_size"
    assert gate.is_killed is False  # not latched


def test_position_size_blocks_overflow_sell_from_short() -> None:
    gate = RiskGate(RiskConfig(capital=100_000, max_position_size=50))
    # Already short 30. Selling 40 more -> projected -70, |.| > 50.
    result = gate.check(_sell(qty=40), _positions(DEMO=-30), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.DENY
    assert result.guard == "position_size"


def test_position_size_allows_exit_toward_flat() -> None:
    gate = RiskGate(RiskConfig(capital=100_000, max_position_size=50))
    # Long 40, selling 10 -> projected +30, |.| <= 50.
    result = gate.check(_sell(qty=10), _positions(DEMO=40), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.ALLOW


def test_position_size_blocks_in_non_latched_mode() -> None:
    """Per-order denial still allows the next (smaller) order through."""
    gate = RiskGate(
        RiskConfig(capital=100_000, max_position_size=50, latch_kill_switch=False)
    )
    big = gate.check(_buy(qty=80), _positions(), daily_pnl=0.0, equity=100_000)
    assert big.decision is Decision.DENY
    assert gate.is_killed is False
    small = gate.check(_buy(qty=10), _positions(), daily_pnl=0.0, equity=100_000)
    assert small.decision is Decision.ALLOW


# --- daily loss guard -----------------------------------------------------


def test_daily_loss_triggers_kill_switch() -> None:
    cfg = RiskConfig(capital=100_000, max_daily_loss=0.02)  # 2% -> -$2000
    gate = RiskGate(cfg)
    # daily_pnl -3000 < -2000 -> trip.
    result = gate.check(_buy(), _positions(), daily_pnl=-3000.0, equity=97_000)
    assert result.decision is Decision.DENY
    assert result.guard == "daily_loss"
    assert gate.is_killed is True
    assert "daily_loss" in (gate.kill_reason or "")


def test_daily_loss_at_exact_limit_is_allowed() -> None:
    """Boundary: daily_pnl == limit is NOT a breach."""
    cfg = RiskConfig(capital=100_000, max_daily_loss=0.02)
    gate = RiskGate(cfg)
    result = gate.check(_buy(), _positions(), daily_pnl=-2000.0, equity=98_000)
    assert result.decision is Decision.ALLOW


def test_kill_switch_latches_subsequent_orders() -> None:
    cfg = RiskConfig(capital=100_000, max_daily_loss=0.02, max_drawdown=0.10)
    gate = RiskGate(cfg)
    gate.check(_buy(), _positions(), daily_pnl=-3000.0, equity=97_000)
    assert gate.is_killed
    # Subsequent benign order still denied.
    next_check = gate.check(_buy(), _positions(), daily_pnl=-3000.0, equity=97_000)
    assert next_check.decision is Decision.DENY
    assert next_check.guard == "kill_switch"


def test_reset_unlatches_kill_switch() -> None:
    cfg = RiskConfig(capital=100_000, max_daily_loss=0.02)
    gate = RiskGate(cfg)
    gate.check(_buy(), _positions(), daily_pnl=-3000.0, equity=97_000)
    assert gate.is_killed
    gate.reset()
    assert gate.is_killed is False
    # And trading works again.
    result = gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.ALLOW


def test_force_kill_external() -> None:
    gate = RiskGate(RiskConfig())
    gate.force_kill("panic button")
    assert gate.is_killed
    result = gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    assert result.decision is Decision.DENY
    assert "manual" in gate.kill_reason


# --- drawdown guard -------------------------------------------------------


def test_drawdown_triggers_kill_switch() -> None:
    cfg = RiskConfig(capital=100_000, max_drawdown=0.10, max_daily_loss=1.0)
    gate = RiskGate(cfg)
    # First call establishes peak at 100_000.
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    # Now equity drops to 89_000 -> drawdown = 11% > 10%.
    result = gate.check(_buy(), _positions(), daily_pnl=0.0, equity=89_000)
    assert result.decision is Decision.DENY
    assert result.guard == "drawdown"
    assert gate.is_killed


def test_drawdown_within_limit_allowed() -> None:
    cfg = RiskConfig(capital=100_000, max_drawdown=0.10, max_daily_loss=1.0)
    gate = RiskGate(cfg)
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    # 5% drawdown — under limit.
    result = gate.check(_buy(), _positions(), daily_pnl=0.0, equity=95_000)
    assert result.decision is Decision.ALLOW


def test_drawdown_peak_tracks_high_water_mark() -> None:
    """Peak should only rise, never fall."""
    cfg = RiskConfig(capital=100_000, max_drawdown=0.10, max_daily_loss=1.0)
    gate = RiskGate(cfg)
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=120_000)
    assert gate.peak_equity == 120_000
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    # Peak stays at 120_000.
    assert gate.peak_equity == 120_000


def test_current_drawdown_property() -> None:
    cfg = RiskConfig(capital=100_000, max_drawdown=0.10, max_daily_loss=1.0)
    gate = RiskGate(cfg)
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=100_000)
    assert gate.current_drawdown == 0.0
    gate.check(_buy(), _positions(), daily_pnl=0.0, equity=90_000)
    assert abs(gate.current_drawdown - 0.10) < 1e-9


# --- guard priority -------------------------------------------------------


def test_latched_kill_switch_takes_precedence_over_other_guards() -> None:
    cfg = RiskConfig(capital=100_000, max_position_size=1, max_daily_loss=0.02)
    gate = RiskGate(cfg)
    # Trip via daily loss.
    gate.check(_buy(), _positions(), daily_pnl=-5_000, equity=95_000)
    assert gate.is_killed
    # Now an order that *would* be denied by position-size too. Kill switch
    # reason should be reported.
    result = gate.check(_buy(qty=1000), _positions(), daily_pnl=-5_000, equity=95_000)
    assert result.decision is Decision.DENY
    assert result.guard == "kill_switch"