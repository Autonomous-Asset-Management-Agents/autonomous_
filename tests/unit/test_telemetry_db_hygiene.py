# tests/unit/test_telemetry_db_hygiene.py
# INF-13 OBS-7 (#2640): db.statement parametrization + cardinality caps at the
# export choke point (core/telemetry_local) + decision-span source hardening
# (core/cloud_logger). Values must be scrubbed BEFORE they are written (the #1371
# load-bearing line), so a future egress (#1457) cannot leak literals / high
# cardinality.
from core.cloud_logger import _decision_span_attributes
from core.telemetry_local import _scrub_attrs, cap_attr, parametrize_db_statement


def test_parametrize_db_statement_strips_string_and_number_literals():
    out = parametrize_db_statement("INSERT INTO trades VALUES ('AAPL', 123.45)")
    assert "AAPL" not in out
    assert "123.45" not in out
    assert "?" in out


def test_parametrize_signed_and_scientific_notation():
    out = parametrize_db_statement("a=-123.45 AND b=1e-5 AND c=+42 AND d=6.02E23")
    for literal in ("-123.45", "1e-5", "+42", "6.02E23", "123.45", "6.02"):
        assert literal not in out, f"leaked: {literal!r} in {out!r}"


def test_parametrize_preserves_identifier_digits():
    # digits INSIDE identifiers must not be parametrized
    assert parametrize_db_statement("SELECT col1, table2.x") == "SELECT col1, table2.x"


def test_parametrize_handles_escaped_quote_string():
    assert parametrize_db_statement("name = 'O''Brien'") == "name = ?"


def test_cap_attr_hashes_bot_decision():
    v = cap_attr("bot.decision", "AAPL=BUY,MSFT=SELL,NVDA=BUY")
    assert v.startswith("sha1:")
    assert "AAPL" not in v and "BUY" not in v


def test_cap_attr_parametrizes_db_statement():
    v = cap_attr("db.statement", "INSERT INTO x VALUES ('SECRET', 9)")
    assert "SECRET" not in v and "9" not in v


def test_cap_attr_leaves_safe_keys_untouched():
    assert cap_attr("db.operation", "insert") == "insert"
    assert cap_attr("http.route", "/portfolio-summary") == "/portfolio-summary"


def test_scrub_attrs_applies_db_hygiene_and_keeps_secret_path_scrub():
    attrs = {
        "db.statement": "INSERT INTO t VALUES ('AAPL', 12)",
        "bot.decision": "AAPL=BUY",
        "db.operation": "insert",
        "db.name": "trades",
        "path": "C:/Users/andre/secret",
    }
    out = _scrub_attrs(attrs)
    # db.statement parametrized
    assert "AAPL" not in out["db.statement"] and "12" not in out["db.statement"]
    # bot.decision hashed
    assert out["bot.decision"].startswith("sha1:")
    # low-cardinality diagnostic keys pass through unchanged
    assert out["db.operation"] == "insert"
    assert out["db.name"] == "trades"
    # existing OS-username path scrub still applied on top
    assert "andre" not in out["path"] and "[user]" in out["path"]


def test_cap_attr_logs_and_falls_back_on_error(monkeypatch, caplog):
    # #2850-review POLICY-01: cap_attr must NOT swallow silently — a failure means a
    # value may reach the export UN-capped (compliance-relevant), so it is logged.
    import core.telemetry_local as tl

    def _boom(_s):
        raise RuntimeError("regex exploded")

    monkeypatch.setattr(tl, "parametrize_db_statement", _boom)
    with caplog.at_level("WARNING", logger="core.telemetry_local"):
        out = tl.cap_attr("db.statement", "INSERT INTO t VALUES ('AAPL')")
    # falls back to the original value (never raises out)
    assert out == "INSERT INTO t VALUES ('AAPL')"
    # and the failure is recorded, not swallowed
    assert any(
        "cap_attr" in r.getMessage() and r.levelname == "WARNING"
        for r in caplog.records
    ), "cap_attr failure was not logged at WARNING"


def test_decision_span_attributes_are_aggregate_only():
    items = [
        {"symbol": "AAPL", "action": "BUY"},
        {"symbol": "MSFT", "action": "SELL"},
    ]
    attrs = _decision_span_attributes(items)
    assert attrs == {"bot.decision_count": 2}
    # no per-symbol / action string is exposed
    assert "AAPL" not in str(attrs) and "BUY" not in str(attrs)
