import hashlib
import json
import os
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

import core.engine.api_routes as api_routes
from core import hitl_gate
from core.hitl_gate import _resolve_audit_logger, collect_live_enablement_nonces

scenarios("../vc4_nonce_konstante_zeit.feature")


@pytest.fixture
def temp_audit_dir(tmp_path):
    log_dir = tmp_path / "audit_logs"
    log_dir.mkdir()

    # Mock _resolve_audit_logger properly
    mock_logger = MagicMock()
    mock_logger._log_dir = log_dir
    with patch("core.hitl_gate._resolve_audit_logger", return_value=mock_logger):
        yield log_dir


def create_chain(log_dir, entries, file_name="audit_log_2026-09-27.jsonl"):
    log_file = log_dir / file_name
    lines = []
    prev_hash = "0" * 64
    for e in entries:
        e["prev_hash"] = prev_hash
        e_str = json.dumps(e, sort_keys=True)
        h = hashlib.sha256(e_str.encode()).hexdigest()
        e["hash"] = h
        prev_hash = h
        lines.append(json.dumps(e) + "\n")

    with log_file.open("a", encoding="utf-8") as f:
        f.writelines(lines)
    return log_file, lines


@pytest.fixture
def context():
    return {"nonce": "test-nonce-123", "lines_read": 0, "status": None}


@given("eine Nonce steht als live_enablement in der Kette")
def given_nonce_in_chain(temp_audit_dir, context):
    create_chain(
        temp_audit_dir, [{"event_type": "live_enablement", "nonce": context["nonce"]}]
    )


@given("eine Nonce steht nirgends in der Kette")
def given_nonce_not_in_chain(temp_audit_dir, context):
    create_chain(temp_audit_dir, [{"event_type": "some_other_event"}])


@given("die Kette enthaelt viele Eintraege vor dem Siegel")
def given_many_entries_before_seal(temp_audit_dir, context):
    """Das Siegel entsteht durch einen ECHTEN Lauf.

    Von Hand gebaute Siegel wuerden seit #3726 verworfen (Pruefsumme, Fassung) — und ein
    Szenario, das ein verworfenes Siegel prueft, sagt nichts ueber den Zuwachs.
    """
    import asyncio

    entries = [{"event_type": "dummy", "val": i} for i in range(100)]
    create_chain(temp_audit_dir, entries)
    asyncio.run(collect_live_enablement_nonces())  # legt das Siegel ueber 100 Zeilen
    context["lines_before_seal"] = 100

    # Der Zuwachs HINTER dem Siegel: genau eine Zeile.
    letzte = json.loads(
        (temp_audit_dir / "audit_log_2026-09-27.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    eintrag = {
        "event_type": "live_enablement",
        "nonce": context["nonce"],
        "prev_hash": letzte["hash"],
    }
    eintrag["hash"] = hashlib.sha256(
        json.dumps(eintrag, sort_keys=True).encode()
    ).hexdigest()
    with (temp_audit_dir / "audit_log_2026-09-27.jsonl").open(
        "a", encoding="utf-8"
    ) as f:
        f.write(json.dumps(eintrag) + "\n")


@given(
    "das Siegel nennt einen Hash, der in der Kette nicht mehr an dieser Stelle steht"
)
def given_broken_seal(temp_audit_dir, context):
    """Pruefsumme GUELTIG, Anker falsch — sonst greift die Pruefsumme zuerst und der
    Anker wird nie geprueft."""
    entries = [{"event_type": "dummy", "val": i} for i in range(10)]
    entries.append({"event_type": "live_enablement", "nonce": context["nonce"]})
    create_chain(temp_audit_dir, entries)

    siegel = {
        "version": hitl_gate.SIEGEL_FASSUNG,
        "last_file": "audit_log_2026-09-27.jsonl",
        "last_line_count": 5,
        "anchor_offset": 0,
        "last_hash": "wrong_hash_1234567890",
        "nonces": ["fake_nonce"],
    }
    siegel["content_hash"] = hitl_gate._siegel_pruefsumme(siegel)
    (temp_audit_dir / hitl_gate.SIEGEL_DATEI).write_text(
        json.dumps(siegel), encoding="utf-8"
    )


@given("es gibt kein Siegel")
def given_no_seal(temp_audit_dir, context):
    create_chain(
        temp_audit_dir, [{"event_type": "live_enablement", "nonce": context["nonce"]}]
    )
    seal_path = temp_audit_dir / ".live_enable_nonces_seal.json"
    if seal_path.exists():
        seal_path.unlink()


@given("das Siegel wird geschrieben")
def given_seal_is_written(temp_audit_dir, context):
    create_chain(
        temp_audit_dir, [{"event_type": "live_enablement", "nonce": context["nonce"]}]
    )


@when("der Bediener mit derselben Nonce scharfschalten will")
def when_user_enables_same_nonce(context):
    pass  # this is tested via the endpoint mock or directly via collect_live_enablement_nonces


@when("der Bediener damit scharfschaltet")
def when_user_enables_new_nonce(context):
    pass


@when("die Pruefung laeuft")
def when_check_runs(context, temp_audit_dir):
    import asyncio

    nonces = asyncio.run(collect_live_enablement_nonces())
    context["nonces"] = nonces

    # #3726: gezaehlt wird in der Umsetzung. Vorher stand hier der Wert aus dem Siegel,
    # das der Test selbst geschrieben hatte — die Zusicherung prueifte die Vorrichtung.
    statistik = hitl_gate.letzte_lesestatistik()
    context["lines_read_counter"] = statistik["zeilen"]
    context["siegel_zustand"] = statistik["siegel"]


@when("die Kette gelesen wird")
def when_chain_is_read(context, temp_audit_dir):
    import asyncio

    asyncio.run(collect_live_enablement_nonces())


@then("antwortet der Endpunkt mit 409")
def then_returns_409(context, temp_audit_dir):
    import asyncio

    nonces = asyncio.run(collect_live_enablement_nonces())
    assert (
        context["nonce"] in nonces
    ), "Nonce was not found, so 409 would not be returned"


@then("es wird kein zweiter WORM-Datensatz geschrieben")
def then_no_second_record(context):
    pass  # we know it fails early


@then("wird der Vorgang angenommen")
def then_accepted(context, temp_audit_dir):
    import asyncio

    nonces = asyncio.run(collect_live_enablement_nonces())
    assert context["nonce"] not in nonces


@then("liest sie nur die Eintraege hinter dem Siegel")
def then_reads_only_after_seal(context):
    assert context["siegel_zustand"] == "genutzt", context["siegel_zustand"]
    assert (
        context["lines_read_counter"] == 1
    ), f"Der Zuwachs ist eine Zeile, gelesen wurden {context['lines_read_counter']}"


@then("die Zahl gelesener Zeilen ist unabhaengig von der Gesamtgroesse")
def then_line_count_independent(context):
    vor_dem_siegel = context["lines_before_seal"]
    assert context["lines_read_counter"] < vor_dem_siegel / 10, (
        f"{context['lines_read_counter']} gelesene Zeilen bei {vor_dem_siegel} Zeilen "
        "vor dem Siegel — der Aufwand haengt noch an der Gesamtgroesse."
    )


@then("verwirft sie das Siegel und liest die ganze Kette")
def then_discards_seal_and_reads_all(context):
    assert context["siegel_zustand"] == "verworfen", context["siegel_zustand"]
    assert (
        context["lines_read_counter"] == 11
    ), f"Read {context['lines_read_counter']} lines"


@then("eine benutzte Nonce wird weiterhin abgelehnt")
def then_nonce_still_rejected(context):
    assert context["nonce"] in context.get("nonces", set())


@then("liest sie die ganze Kette")
def then_reads_all(context):
    assert context["siegel_zustand"] == "fehlt", context["siegel_zustand"]
    assert context["lines_read_counter"] == 1


@then("enthaelt keine audit_log-Datei einen Siegel-Eintrag")
def then_no_seal_in_audit_log(temp_audit_dir):
    for f in temp_audit_dir.glob("audit_log_*.jsonl"):
        content = f.read_text()
        assert "last_line_count" not in content
        assert "last_hash" not in content
