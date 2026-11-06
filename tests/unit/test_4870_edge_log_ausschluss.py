"""#4870 (#4581 S2, ADR-026 Weg D) — keine IP der Besucher in den Protokollen der Zählung.

Owner-Entscheid 28.10.2026. Cloud Run schreibt zu jeder Anfrage automatisch ein Request-Log
(``run.googleapis.com/requests``, Ressource ``cloud_run_revision``, Feld ``httpRequest.remoteIp``);
eine Function der 2. Generation ist ein Cloud-Run-Dienst mit ihrem Namen in Kleinbuchstaben.
Ein Ausschluss an der ``_Default``-Senke (``google_logging_project_exclusion``) verhindert, dass
diese Einträge im Log-Bucket landen. Die Function läuft im Firebase-Projekt (``.firebaserc``),
nicht im Terraform-Projekt — der Ausschluss braucht also ein ausdrückliches ``project``.
Statisch geprüft, mit begrenzten Regexen.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_REPO = Path(__file__).resolve().parents[3]
_TF = _REPO / "infra" / "terraform"


def _block(text: str, kopf: str) -> str | None:
    treffer = re.search(rf"^{kopf} \{{", text, re.M)
    if not treffer:
        return None
    tiefe, start = 0, treffer.end() - 1
    for i in range(start, len(text)):
        tiefe += {"{": 1, "}": -1}.get(text[i], 0)
        if tiefe == 0:
            return text[start : i + 1]
    return None


def _ausschluss() -> str:
    for datei in sorted(_TF.glob("*.tf")):
        block = _block(
            datei.read_text(encoding="utf-8"),
            r'resource "google_logging_project_exclusion" "edge_request_logs"',
        )
        if block:
            return block
    pytest.fail(
        "google_logging_project_exclusion.edge_request_logs fehlt in infra/terraform/"
    )


def test_ausschluss_trifft_genau_die_request_logs_des_dienstes_edge():
    block = _ausschluss()
    filter_ = (
        re.search(r'filter\s*=\s*"((?:[^"\\]|\\.)*)"', block)
        .group(1)
        .replace('\\"', '"')
    )
    assert 'resource.type="cloud_run_revision"' in filter_
    assert 'resource.labels.service_name="edge"' in filter_
    assert 'LOG_ID("run.googleapis.com/requests")' in filter_
    # nur Request-Logs: stdout/stderr der Function bleiben für die Fehlersuche
    assert "stdout" not in filter_ and "stderr" not in filter_


def test_ausschluss_liegt_im_firebase_projekt_der_function():
    block = _ausschluss()
    assert re.search(r"project\s*=\s*var\.firebase_project_id", block)
    variablen = (_TF / "variables.tf").read_text(encoding="utf-8")
    default = re.search(
        r'variable "firebase_project_id" \{[^}]*default\s*=\s*"([^"]+)"',
        variablen,
        re.S,
    )
    assert default, "variable firebase_project_id mit Default fehlt"
    firebaserc = json.loads((_REPO / ".firebaserc").read_text(encoding="utf-8"))
    assert default.group(1) == firebaserc["projects"]["default"]


# #4907 (Owner-Entscheid 28.10.2026, ADR-026): Umami/GrowthBook werden nicht gebaut; die Zählung
# schreibt Logzeilen. Der IP-Ausschluss gilt deshalb immer, nicht erst mit `enable_analytics`.
def test_ausschluss_gilt_ohne_analytics_schalter():
    block = _ausschluss()
    assert "count" not in block
    assert "enable_analytics" not in block
    assert not re.search(r"disabled\s*=\s*true", block)


def _resource(typ: str, name: str) -> str:
    text = (_TF / "edge_logging.tf").read_text(encoding="utf-8")
    block = _block(text, rf'resource "{typ}" "{name}"')
    assert block, f"{typ}.{name} fehlt in infra/terraform/edge_logging.tf"
    return block


def _filter(block: str) -> str:
    return (
        re.search(r'filter\s*=\s*"((?:[^"\\]|\\.)*)"', block)
        .group(1)
        .replace('\\"', '"')
    )


_ZAEHLZEILEN = (
    'resource.type="cloud_run_revision" AND resource.labels.service_name="edge"'
    ' AND jsonPayload.message="aaa_count"'
)


def test_4907_zaehlzeilen_liegen_in_der_eu_90_tage():
    block = _resource("google_logging_project_bucket_config", "website_counts")
    assert re.search(r'location\s*=\s*"europe-west3"', block)
    assert re.search(r'bucket_id\s*=\s*"website-counts"', block)
    assert re.search(r"retention_days\s*=\s*90\b", block)
    assert re.search(r"project\s*=\s*var\.firebase_project_id", block)


def test_4907_senke_leitet_genau_die_zaehlzeilen_in_den_bucket():
    block = _resource("google_logging_project_sink", "website_counts")
    assert _filter(block) == _ZAEHLZEILEN
    assert "google_logging_project_bucket_config.website_counts.id" in block
    # Marker identisch mit dem, den die Function schreibt
    core = (_REPO / "functions" / "src" / "edge-core.mjs").read_text(encoding="utf-8")
    assert 'message: "aaa_count"' in core


def test_4907_zaehlzeilen_nicht_zusaetzlich_im_globalen_default_bucket():
    block = _resource("google_logging_project_exclusion", "website_counts_default")
    assert _filter(block) == _ZAEHLZEILEN
    assert re.search(r"project\s*=\s*var\.firebase_project_id", block)
