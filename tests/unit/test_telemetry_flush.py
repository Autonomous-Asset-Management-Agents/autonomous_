# tests/unit/test_telemetry_flush.py
# INF-13 P2 (#1456): the flush daemon is DORMANT by default — egress requires
# opt-in consent AND the egress master switch (both default OFF, #1368 Gate ④),
# plus a wired transport (P3 #1457). With the gate off it makes ZERO egress.
import json
import os

from core.telemetry_flush import TelemetryFlusher, egress_allowed


def _seed(d, records):
    p = os.path.join(str(d), "telemetry.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r) + "\n")
    return p


def test_egress_allowed_false_by_default():
    # Default config: both consent + egress flag OFF.
    assert egress_allowed() is False


def test_egress_allowed_requires_both(monkeypatch):
    import config

    monkeypatch.setattr(config, "TELEMETRY_EGRESS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "TELEMETRY_CRASH_CONSENT", False, raising=False)
    assert egress_allowed() is False
    monkeypatch.setattr(config, "TELEMETRY_CRASH_CONSENT", True, raising=False)
    assert egress_allowed() is True


def test_flusher_dormant_by_default_zero_egress(tmp_path):
    d = tmp_path / "telemetry"
    d.mkdir()
    _seed(d, [{"name": "crash.x", "end_time": 1}])
    calls = []

    def sender(records):
        calls.append(records)
        return True

    # gate defaults to egress_allowed() -> False -> sender must never be called.
    f = TelemetryFlusher(str(d), sender=sender)
    assert f.flush() == 0
    assert calls == []  # ZERO egress when the gate is off


def test_flusher_sends_and_clears_when_gate_open(tmp_path):
    d = tmp_path / "telemetry"
    d.mkdir()
    p = _seed(d, [{"name": "a"}, {"name": "b"}])
    seen = []
    f = TelemetryFlusher(
        str(d), sender=lambda recs: (seen.extend(recs) or True), gate=lambda: True
    )
    assert f.flush() == 2
    assert [r["name"] for r in seen] == ["a", "b"]
    assert os.path.getsize(p) == 0  # store cleared after a successful send


def test_flusher_retains_on_sender_failure(tmp_path):
    d = tmp_path / "telemetry"
    d.mkdir()
    p = _seed(d, [{"name": "a"}])
    f = TelemetryFlusher(str(d), sender=lambda recs: False, gate=lambda: True)
    assert f.flush() == 0
    assert os.path.getsize(p) > 0  # kept for retry (offline-first)


def test_flusher_no_sender_is_noop(tmp_path):
    # P2: no OTLP transport is wired yet (that is P3) -> dormant even if gate open.
    d = tmp_path / "telemetry"
    d.mkdir()
    _seed(d, [{"name": "a"}])
    f = TelemetryFlusher(str(d), sender=None, gate=lambda: True)
    assert f.flush() == 0


def test_flusher_never_raises_on_garbage(tmp_path):
    d = tmp_path / "telemetry"
    d.mkdir()
    p = os.path.join(str(d), "telemetry.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("not json\n")
    f = TelemetryFlusher(str(d), sender=lambda recs: True, gate=lambda: True)
    assert f.flush() == 0  # unparseable dropped, never raises
