# tests/unit/test_form4_direction.py
"""RQ-1 B3b (#1536): Form 4 buy/sell direction parser + fetch (pure + mocked network)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from core.specialist.form4_direction import (
    classify_form4_direction,
    parse_form4_direction,
)


def _txn(code, shares):
    return (
        "<nonDerivativeTransaction><transactionCoding>"
        f"<transactionCode>{code}</transactionCode></transactionCoding>"
        f"<transactionAmounts><transactionShares><value>{shares}</value>"
        "</transactionShares></transactionAmounts></nonDerivativeTransaction>"
    )


def _run(coro):
    return asyncio.run(coro)


class TestParseForm4Direction:
    def test_purchase_is_buy(self):
        assert parse_form4_direction(_txn("P", 1000)) == "buy"

    def test_sale_is_sell(self):
        assert parse_form4_direction(_txn("S", 500)) == "sell"

    def test_grant_is_neutral(self):
        assert parse_form4_direction(_txn("A", 10000)) == "neutral"

    def test_option_exercise_is_neutral(self):
        assert parse_form4_direction(_txn("M", 5000)) == "neutral"

    def test_net_buy_when_buys_exceed_sells(self):
        assert parse_form4_direction(_txn("P", 1000) + _txn("S", 400)) == "buy"

    def test_net_sell_when_sells_exceed_buys(self):
        assert parse_form4_direction(_txn("P", 200) + _txn("S", 900)) == "sell"

    def test_equal_shares_is_mixed(self):
        assert parse_form4_direction(_txn("P", 500) + _txn("S", 500)) == "mixed"

    def test_comma_shares_parsed(self):
        assert parse_form4_direction(_txn("P", "1,234,567")) == "buy"

    def test_empty_and_garbage_are_neutral(self):
        assert parse_form4_direction("") == "neutral"
        assert parse_form4_direction("<garbage/>") == "neutral"


class TestClassifyForm4Direction:
    def _client(self, text, status=200):
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        c = MagicMock()
        c.get = AsyncMock(return_value=resp)
        return c

    def test_fetches_and_parses_buy(self):
        c = self._client(_txn("P", 1000))
        assert _run(classify_form4_direction(c, "http://x/form4.xml")) == "buy"

    def test_http_error_is_neutral(self):
        c = self._client("", status=404)
        assert _run(classify_form4_direction(c, "http://x/form4.xml")) == "neutral"

    def test_network_exception_is_neutral(self):
        c = MagicMock()
        c.get = AsyncMock(side_effect=RuntimeError("boom"))
        assert _run(classify_form4_direction(c, "http://x/form4.xml")) == "neutral"
