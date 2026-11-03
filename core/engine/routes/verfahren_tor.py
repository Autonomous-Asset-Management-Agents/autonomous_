"""Live-Tor fuer den Verfahrenswechsel (#4989, Epic #4983).

``/api/trading-settings`` und ``/api/strategies/{name}/assign-live`` rufen dieselbe Regel
(``core/trading_settings.py::pruefe_verfahrenswechsel``), jeweils nach dem Diff und vor der
Nonce: eine Ablehnung schreibt nichts und verbraucht nichts.

Die Routen liegen in ``routes/steuerung.py``. Eine eigene Datei, weil Routen und Helfer
zusammen ueber der Datei-Schwelle von 1000 Zeilen laegen (``tests/architecture/vertrag.toml``,
``[groessen]``) — Muster ``routes/strategie_zuweisung.py``. Dieses Modul hat keine Routen.

Plan: ``docs/4989-verfahrenswechsel-im-live-betrieb-braucht-eine-e/implementation_plan.md``.
"""

from __future__ import annotations

from core.engine import api_routes as ar
from core.engine.fehler import CodedHTTPException


def pruefe(changes: list, acknowledgment: str, nonce: str) -> None:
    """Die Live-Regel, fail-closed: nur ein echtes ``True`` zaehlt als Paper.

    Direkter Attributzugriff (Ratsche ``tests/architecture/vertrag.toml``). Der Span traegt
    wie ``settings.apply`` nur Keys und Nonce, nie alte oder neue Werte.
    """
    from core import trading_settings as ts

    paper = ar.config.PAPER_TRADING is True
    try:
        ts.pruefe_verfahrenswechsel(changes, acknowledgment, paper=paper)
    except ts.VerfahrenswechselUnbestaetigtError as exc:
        try:
            with ar._tracer.start_as_current_span("settings.apply_rejected") as _span:
                _span.set_attribute("keys", ",".join(sorted(c["key"] for c in changes)))
                _span.set_attribute("nonce", nonce)
                _span.set_attribute("reason", exc.code)
                _span.set_attribute("actor", "operator")
        except (
            Exception
        ) as span_exc:  # noqa: BLE001 — Telemetrie aendert die Ablehnung nie
            ar.logger.warning(
                "[SETTINGS] settings.apply_rejected span failed: %s", span_exc
            )
        raise CodedHTTPException(
            409, str(exc), code=exc.code, params=exc.params
        ) from exc
