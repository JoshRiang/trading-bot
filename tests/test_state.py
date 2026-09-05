"""Tests for the OrderState transition graph."""

from __future__ import annotations

import pytest

from bot.state import (
    IllegalTransitionError,
    OrderState,
    TERMINAL_STATES,
    assert_legal_transition,
    is_legal_transition,
)


# (from, to) pairs that MUST be legal.
LEGAL_PAIRS = [
    (OrderState.PENDING, OrderState.SUBMITTED),
    (OrderState.PENDING, OrderState.CANCELLED),
    (OrderState.PENDING, OrderState.REJECTED),
    (OrderState.SUBMITTED, OrderState.FILLED),
    (OrderState.SUBMITTED, OrderState.CANCELLED),
    (OrderState.SUBMITTED, OrderState.REJECTED),
]

# (from, to) pairs that MUST be illegal.
ILLEGAL_PAIRS = [
    (OrderState.PENDING, OrderState.FILLED),
    (OrderState.SUBMITTED, OrderState.PENDING),
    (OrderState.FILLED, OrderState.CANCELLED),
    (OrderState.CANCELLED, OrderState.PENDING),
    (OrderState.REJECTED, OrderState.SUBMITTED),
    # No-op transitions are illegal.
    (OrderState.PENDING, OrderState.PENDING),
    (OrderState.FILLED, OrderState.FILLED),
]


@pytest.mark.parametrize("src,dst", LEGAL_PAIRS)
def test_legal_transitions(src: OrderState, dst: OrderState) -> None:
    assert is_legal_transition(src, dst) is True
    assert_legal_transition(src, dst)  # does not raise


@pytest.mark.parametrize("src,dst", ILLEGAL_PAIRS)
def test_illegal_transitions(src: OrderState, dst: OrderState) -> None:
    assert is_legal_transition(src, dst) is False
    with pytest.raises(IllegalTransitionError):
        assert_legal_transition(src, dst)


def test_terminal_states_are_absorbing() -> None:
    for terminal in TERMINAL_STATES:
        for other in OrderState:
            if other == terminal:
                continue
            assert is_legal_transition(terminal, other) is False, (
                f"terminal state {terminal} should not transition to {other}"
            )


def test_terminal_states_set() -> None:
    assert TERMINAL_STATES == frozenset(
        {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}
    )


def test_illegal_transition_error_carries_states() -> None:
    err = IllegalTransitionError(OrderState.PENDING, OrderState.FILLED)
    assert err.src is OrderState.PENDING
    assert err.dst is OrderState.FILLED
    assert "PENDING" in str(err) and "FILLED" in str(err)


def test_full_happy_path() -> None:
    """Walk PENDING → SUBMITTED → FILLED."""
    src = OrderState.PENDING
    assert_legal_transition(src, OrderState.SUBMITTED)
    src = OrderState.SUBMITTED
    assert_legal_transition(src, OrderState.FILLED)
    # And FILLED is terminal — no further moves.
    with pytest.raises(IllegalTransitionError):
        assert_legal_transition(OrderState.FILLED, OrderState.CANCELLED)


def test_rejection_path() -> None:
    """PENDING → REJECTED (e.g. unknown symbol at the broker)."""
    assert_legal_transition(OrderState.PENDING, OrderState.REJECTED)
    # And from SUBMITTED too (e.g. broker-side risk reject).
    assert_legal_transition(OrderState.SUBMITTED, OrderState.REJECTED)