
# Maintenance: last reviewed 2026-09-07 (daily improvement cycle)
"""Tests for the in-memory PaperBroker."""

from __future__ import annotations

import pytest

from bot.brokers.base import BrokerError, BrokerOrderRejected
from bot.brokers.paper import PaperBroker, make_order_id
from bot.orders import Order, OrderSide, OrderState, OrderType, Quote


def _market_buy(symbol: str = "DEMO", qty: float = 10.0, oid: str = "buy-1") -> Order:
    return Order(
        id=oid,
        symbol=symbol,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        quantity=qty,
    )


def _market_sell(symbol: str = "DEMO", qty: float = 10.0, oid: str = "sell-1") -> Order:
    return Order(
        id=oid,
        symbol=symbol,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=qty,
    )


@pytest.fixture
def broker() -> PaperBroker:
    b = PaperBroker()
    b.connect()
    b.set_cash(100_000.0)
    b.set_quote(Quote(symbol="DEMO", bid=99.5, ask=100.5, last=100.0))
    return b


# --- connection -----------------------------------------------------------


def test_connect_disconnect_idempotent(broker: PaperBroker) -> None:
    broker.connect()  # already connected — should not raise
    broker.disconnect()
    broker.disconnect()


def test_submit_without_connect_raises() -> None:
    b = PaperBroker()
    b.set_quote(Quote(symbol="DEMO", bid=100.0, ask=100.0, last=100.0))
    with pytest.raises(BrokerError):
        b.submit_order(_market_buy())


# --- market orders --------------------------------------------------------


def test_market_buy_fills_instantly_at_last(broker: PaperBroker) -> None:
    order = broker.submit_order(_market_buy(qty=10))
    assert order.state is OrderState.FILLED
    assert order.filled_quantity == 10
    assert order.avg_fill_price == 100.0


def test_market_sell_short_fills(broker: PaperBroker) -> None:
    order = broker.submit_order(_market_sell(qty=10))
    assert order.state is OrderState.FILLED
    positions = broker.get_positions()
    assert "DEMO" in positions
    assert positions["DEMO"].quantity == -10


def test_positions_track_long_then_flat(broker: PaperBroker) -> None:
    broker.submit_order(_market_buy(qty=10))
    broker.submit_order(_market_sell(qty=10))
    positions = broker.get_positions()
    # Net flat -> no position tracked.
    assert positions == {}
    assert broker.cash() == pytest.approx(100_000.0, abs=1e-6)


def test_duplicate_order_id_rejected(broker: PaperBroker) -> None:
    broker.submit_order(_market_buy(qty=10))
    with pytest.raises(BrokerError):
        broker.submit_order(_market_buy(qty=5, oid="buy-1"))


def test_unknown_symbol_rejects_with_no_quote(broker: PaperBroker) -> None:
    with pytest.raises(BrokerOrderRejected):
        broker.submit_order(_market_buy(symbol="MISSING"))
    # Order is stored as REJECTED so introspection works.
    rejected = [o for o in broker.list_orders() if o.symbol == "MISSING"]
    assert rejected and rejected[0].state is OrderState.REJECTED


# --- limit orders ---------------------------------------------------------


def test_limit_order_stays_submitted_until_price_hits(broker: PaperBroker) -> None:
    order = Order(
        id="lim-1",
        symbol="DEMO",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=10,
        limit_price=99.0,
    )
    broker.submit_order(order)
    assert order.state is OrderState.SUBMITTED

    # Push a quote still above limit — no fill.
    trades = broker.advance(Quote(symbol="DEMO", bid=99.5, ask=99.6, last=99.5))
    assert trades == []
    assert order.state is OrderState.SUBMITTED

    # Now push a quote at or below the limit — fills.
    trades = broker.advance(Quote(symbol="DEMO", bid=98.5, ask=98.6, last=98.5))
    assert len(trades) == 1
    assert trades[0].price == 99.0  # filled at limit, not market
    assert order.state is OrderState.FILLED


def test_limit_sell_fills_when_price_rises(broker: PaperBroker) -> None:
    order = Order(
        id="lim-2",
        symbol="DEMO",
        side=OrderSide.SELL,
        type=OrderType.LIMIT,
        quantity=10,
        limit_price=101.0,
    )
    broker.submit_order(order)
    assert order.state is OrderState.SUBMITTED
    trades = broker.advance(Quote(symbol="DEMO", bid=101.0, ask=101.1, last=101.0))
    assert len(trades) == 1
    assert order.state is OrderState.FILLED


# --- cancel ---------------------------------------------------------------


def test_cancel_pending_or_submitted_order(broker: PaperBroker) -> None:
    order = Order(
        id="lim-3",
        symbol="DEMO",
        side=OrderSide.BUY,
        type=OrderType.LIMIT,
        quantity=10,
        limit_price=50.0,  # far away — will not fill
    )
    broker.submit_order(order)
    assert order.state is OrderState.SUBMITTED
    cancelled = broker.cancel_order("lim-3")
    assert cancelled.state is OrderState.CANCELLED


def test_cancel_unknown_order_raises(broker: PaperBroker) -> None:
    with pytest.raises(BrokerError):
        broker.cancel_order("nope")


def test_cancel_filled_is_idempotent(broker: PaperBroker) -> None:
    order = broker.submit_order(_market_buy(qty=5))
    assert order.state is OrderState.FILLED
    again = broker.cancel_order(order.id)
    assert again.state is OrderState.FILLED  # unchanged


# --- introspection -------------------------------------------------------


def test_list_orders_and_trades(broker: PaperBroker) -> None:
    broker.submit_order(_market_buy(qty=10, oid="a"))
    broker.submit_order(_market_buy(qty=5, oid="b"))
    assert len(broker.list_orders()) == 2
    assert len(broker.list_trades()) == 2


def test_make_order_id_unique() -> None:
    a = make_order_id()
    b = make_order_id()
    assert a != b
    assert a.startswith("ord-")


def test_get_quote_raises_when_missing(broker: PaperBroker) -> None:
    with pytest.raises(BrokerError):
        broker.get_quote("NOPE")


def test_cash_decreases_on_buy(broker: PaperBroker) -> None:
    broker.submit_order(_market_buy(qty=10))  # @ 100 = -$1000
    assert broker.cash() == pytest.approx(99_000.0, abs=1e-6)


def test_cash_increases_on_sell_from_flat(broker: PaperBroker) -> None:
    broker.submit_order(_market_sell(qty=10))  # short @ 100 = +$1000
    assert broker.cash() == pytest.approx(101_000.0, abs=1e-6)