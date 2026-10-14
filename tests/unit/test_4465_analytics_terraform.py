"""#4465 — Analytics-Backend (ADR-026: GrowthBook + Umami) ist per Terraform beschrieben.

Plan: ``docs/4465-self-hosted-analytics-backend-per-terraform-bere/implementation_plan.md``.

``terraform validate`` (CI: ``.github/workflows/terraform.yml``) prueft nur die Syntax. Die
Gherkin-Abnahme verlangt mehr, und das haelt dieser Waechter statisch fest:

- alles haengt an ``enable_analytics`` (Default ``false``) — die CI wendet ``staging.tfvars``
  auf PRs an, ein ungeschalteter Dienst wuerde dort sofort Kosten erzeugen;
- EU-Region nur ueber ``var.region``, Secrets in Secret Manager mit Replikation in der Region;
- kein oeffentlicher Admin-Zugang: Cloud Run nur ueber den Load Balancer, jedes Backend
  ausser den benannten Ingest-/Health-Pfaden hinter IAP;
- Uptime-Checks in ``monitoring.tf``.

Wie ``test_terraform_ignore_changes.py`` mit begrenzten Regexen statt HCL-Parser (keine
neue Abhaengigkeit).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_TF = Path(__file__).resolve().parents[3] / "infra" / "terraform"
_ANALYTICS = _TF / "analytics.tf"
_MONITORING = _TF / "monitoring.tf"

# Die einzigen Pfade, die ohne IAP erreichbar sein duerfen (ADR-026 + Health-Check).
_OEFFENTLICHE_PFADE = {"/script.js", "/api/send", "/api/heartbeat", "/healthcheck"}


def _text(pfad: Path = _ANALYTICS) -> str:
    assert pfad.is_file(), f"fehlt: {pfad.name}"
    return pfad.read_text(encoding="utf-8")


def _bloecke(text: str, kopf: str) -> dict[str, str]:
    """Top-Level-Bloecke ``<kopf> "<typ>" "<name>" { ... }`` je ``typ.name``, klammergenau."""
    bloecke = {}
    for treffer in re.finditer(rf'^{kopf} "([\w-]+)" "([\w-]+)" \{{', text, re.M):
        tiefe, start = 0, treffer.end() - 1
        for i in range(start, len(text)):
            tiefe += {"{": 1, "}": -1}.get(text[i], 0)
            if tiefe == 0:
                bloecke[f"{treffer.group(1)}.{treffer.group(2)}"] = text[start : i + 1]
                break
    return bloecke


def test_schalter_existiert_und_ist_aus():
    variablen = (_TF / "variables.tf").read_text(encoding="utf-8")
    treffer = re.search(r'variable "enable_analytics" \{(.*?)\n\}', variablen, re.S)
    assert treffer, "variable enable_analytics fehlt in variables.tf"
    assert re.search(r"default\s*=\s*false", treffer.group(1))


def test_jede_ressource_haengt_am_schalter():
    ressourcen = _bloecke(_text(), "resource")
    assert ressourcen, "analytics.tf beschreibt keine Ressource"
    ungeschaltet = [
        name
        for name, block in ressourcen.items()
        if not re.search(r"(count|for_each)\s*=\s*var\.enable_analytics", block)
    ]
    assert not ungeschaltet, f"ohne enable_analytics: {ungeschaltet}"


@pytest.mark.parametrize("datei", ["staging.tfvars", "prod.tfvars"])
def test_tfvars_schalten_nicht_ein(datei):
    # Einschalten ist ein menschlicher Apply auf geteilter Infrastruktur, kein Merge.
    assert not re.search(
        r"enable_analytics\s*=\s*true", (_TF / datei).read_text(encoding="utf-8")
    )


def test_dienste_laufen_in_der_eu_region():
    text = _text()
    ressourcen = _bloecke(text, "resource")
    for typ in ("google_cloud_run_v2_service", "google_sql_database_instance"):
        bloecke = {n: b for n, b in ressourcen.items() if n.startswith(typ + ".")}
        assert bloecke, f"kein {typ}"
        for name, block in bloecke.items():
            assert re.search(r"(location|region)\s*=\s*var\.region", block), name
    assert not re.search(
        r'"(us|asia|northamerica|southamerica|australia|me|africa)-\w+', text
    )


def test_schluessel_liegen_in_secret_manager_in_der_region():
    ressourcen = _bloecke(_text(), "resource")
    secrets = [
        b
        for n, b in ressourcen.items()
        if n.startswith("google_secret_manager_secret.")
    ]
    assert secrets, "kein Secret-Manager-Secret"
    for block in secrets:
        assert "user_managed" in block, block[:80]
        assert re.search(r"location\s*=\s*var\.region", block), block[:80]
    for name, block in ressourcen.items():
        if name.startswith("google_cloud_run_v2_service."):
            assert (
                "secret_key_ref" in block
            ), f"{name} liest keine Secrets aus Secret Manager"
            # Klartext-Env darf weder Passwort noch Secret-Wert tragen.
            for wert in re.findall(r"\bvalue\s*=\s*(.+)", block):
                assert (
                    "random_password" not in wert and "local.analytics_pw" not in wert
                ), wert


def test_kein_oeffentlicher_admin_zugang():
    ressourcen = _bloecke(_text(), "resource")
    dienste = {
        n: b
        for n, b in ressourcen.items()
        if n.startswith("google_cloud_run_v2_service.")
    }
    for name, block in dienste.items():
        assert re.search(
            r'ingress\s*=\s*"INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"', block
        ), name
    backends = {
        n: b
        for n, b in ressourcen.items()
        if n.startswith("google_compute_backend_service.")
    }
    assert backends, "kein Load-Balancer-Backend"
    for name, block in backends.items():
        if name.endswith("_public"):
            assert "iap {" not in block, name
        else:
            assert "iap {" in block, f"{name} ohne IAP"
    url_map = next(
        b for n, b in ressourcen.items() if n.startswith("google_compute_url_map.")
    )
    # Jede path_rule fuehrt auf ein *_public-Backend und nur ueber die erlaubten Pfade;
    # default_service zeigt nie auf ein *_public-Backend.
    for regel in re.findall(r"path_rule \{(.*?)\n\s*\}", url_map, re.S):
        pfade = set(re.findall(r'"(/[^"]*)"', regel))
        assert pfade <= _OEFFENTLICHE_PFADE, pfade
        assert "_public" in regel, regel
    for default in re.findall(r"default_service\s*=\s*(\S+)", url_map):
        assert "_public" not in default, default


def test_uptime_checks_in_monitoring():
    checks = _bloecke(_text(_MONITORING), "resource")
    analytics = {
        n: b
        for n, b in checks.items()
        if n.startswith("google_monitoring_uptime_check_config.")
        and "enable_analytics" in b
    }
    pfade = {
        p for b in analytics.values() for p in re.findall(r'path\s*=\s*"([^"]+)"', b)
    }
    assert {"/api/heartbeat", "/healthcheck"} <= pfade, pfade


def test_zustandskritische_ressourcen_haben_prevent_destroy():
    ressourcen = _bloecke(_text(), "resource")
    fuer_schutz = [
        "google_sql_database_instance.analytics",
        "google_compute_global_address.analytics",
    ]
    for schluessel in fuer_schutz:
        assert schluessel in ressourcen, f"{schluessel} fehlt"
        assert re.search(
            r"prevent_destroy\s*=\s*true", ressourcen[schluessel]
        ), f"prevent_destroy fehlt in {schluessel}"
