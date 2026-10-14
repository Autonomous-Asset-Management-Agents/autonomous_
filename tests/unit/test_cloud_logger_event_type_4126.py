"""#4126 Teil 1b (Plan §10c): ``log_compliance_event`` nimmt einen eigenen ``event_type``.

Bestehende Aufrufer (``ComplianceGuardian._log_audit``) bleiben bei ``compliance_check``.
"""

import pytest

from core.cloud_logger import CloudLogger

pytestmark = pytest.mark.vc0


def _eintrag(**kw):
    logger = CloudLogger.__new__(CloudLogger)
    import queue

    logger._compliance_queue = queue.Queue()
    CloudLogger.log_compliance_event(
        logger, order={"symbol": "AAPL", "side": "buy", "quantity": 2}, **kw
    )
    return logger._compliance_queue.get_nowait()


def test_ohne_angabe_bleibt_compliance_check():
    assert _eintrag(approved=True, reason="ok")["event_type"] == "compliance_check"


def test_eigener_event_type_wird_uebernommen():
    eintrag = _eintrag(
        approved=False, reason="trading_halted", event_type="gateway_decision"
    )
    assert eintrag["event_type"] == "gateway_decision"
    assert eintrag["details_json"]["order"]["symbol"] == "AAPL"
