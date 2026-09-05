"""Order state machine.

PENDING -> SUBMITTED -> FILLED
                       \\
                        -> CANCELLED
                       \\
                        -> REJECTED

A move is only legal if it appears in ``LEGAL_TRANSITIONS``. Any other move
raises ``IllegalTransitionError``. Terminal states (FILLED, CANCELLED, REJECTED)
are absorbing — nothing leaves them.
"""

from __future__ import annotations

from enum import Enum


class OrderState(str, Enum):
    PENDING = "PENDING"      # local-only, not yet at broker
    SUBMITTED = "SUBMITTED"  # accepted by broker, awaiting fill
    FILLED = "FILLED"        # fully executed
    CANCELLED = "CANCELLED"  # killed before fill
    REJECTED = "REJECTED"    # broker refused


# (from, to) -> True means the transition is legal.
LEGAL_TRANSITIONS: dict[tuple[OrderState, OrderState], bool] = {
    (OrderState.PENDING, OrderState.SUBMITTED): True,
    (OrderState.PENDING, OrderState.CANCELLED): True,
    (OrderState.PENDING, OrderState.REJECTED): True,
    (OrderState.SUBMITTED, OrderState.FILLED): True,
    (OrderState.SUBMITTED, OrderState.CANCELLED): True,
    (OrderState.SUBMITTED, OrderState.REJECTED): True,
    # Terminal states are absorbing; nothing leaves them.
}


TERMINAL_STATES: frozenset[OrderState] = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}
)


class IllegalTransitionError(Exception):
    """Raised when an order is asked to transition outside the legal graph."""

    def __init__(self, src: OrderState, dst: OrderState):
        self.src = src
        self.dst = dst
        super().__init__(f"illegal order state transition: {src.value} -> {dst.value}")


def is_legal_transition(src: OrderState, dst: OrderState) -> bool:
    """Return True iff ``src -> dst`` is a permitted state move."""
    if src == dst:
        return False  # no-op transitions are not legal moves
    return LEGAL_TRANSITIONS.get((src, dst), False)


def assert_legal_transition(src: OrderState, dst: OrderState) -> None:
    if not is_legal_transition(src, dst):
        raise IllegalTransitionError(src, dst)