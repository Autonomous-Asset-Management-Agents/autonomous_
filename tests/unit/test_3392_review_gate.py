"""#3392 (ARC-E3) — Review-Gate auf Kapitalschutz-Pfaden, Teil A.

1. Jede Datei der engsten Kapitalschutz-Liste ist in ``.github/CODEOWNERS`` einem Menschen
   zugeordnet — und der Abgleich schlaegt fehl, sobald eine Datei ohne Eintrag hinzukommt.
2. Der Order-Deckel ist per Umgebungsvariable nicht ueber die ratifizierte Obergrenze
   (``MAX_ORDER_VALUE_CEILING``) aufhebbar — auch nicht ueber den Konstruktor-Pfad, der
   ``apply_policy`` nicht durchlaeuft. Unterhalb der Obergrenze bleibt er kundenseitig
   einstellbar (Owner-Entscheid 05.09.2026, #3220).

Plan: ``docs/3392-codeowners-ruleset-verhaltens-gate/implementation_plan.md``.
"""

from __future__ import annotations

import fnmatch
import logging
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc4

REPO = Path(__file__).resolve().parents[3]
CODEOWNERS = REPO / ".github" / "CODEOWNERS"

# Die engste Kapitalschutz-Liste: Tor, Absendestelle, Halt, Deckel und ihre Werte.
# config.py und config.oss.py stehen beide darin (BORA: kein Pfad, der nur in einer
# Edition geschuetzt ist).
KAPITALSCHUTZ = (
    "ai_trading_bot/core/compliance.py",
    "ai_trading_bot/core/risk_manager.py",
    "ai_trading_bot/core/risk_vorpruefung.py",  # #4265: Vorpruefung (Halt, VIX, KI-Regeln)
    "ai_trading_bot/core/risk_deckel.py",  # #4267: Deckel der Bemessung
    "ai_trading_bot/core/risk_bemessung.py",  # #4268: Bemessung (Skalierer, Zielgroesse)
    "ai_trading_bot/core/risk_konto.py",  # #4269: Konto, Halt, Liquidation (Breaker)
    "ai_trading_bot/core/risk_skalierer.py",  # #4271: Skalierer und Audit-Spiegel
    "ai_trading_bot/core/kill_switch.py",
    "ai_trading_bot/core/validation.py",
    "ai_trading_bot/core/engine/order_executor.py",
    "ai_trading_bot/core/engine/signal_uebergabe.py",  # #3819: Compliance und Absendung
    "ai_trading_bot/core/engine/absendung_nachlauf.py",  # #3821: Nachbuchen nach dem Broker
    # #4431 Teil 1c: die Order-Pfad-Module aus ARC-E6 H-1 (kapitalschutz.toml, #4432).
    "ai_trading_bot/core/engine/absendung_tor.py",
    "ai_trading_bot/core/engine/absendung_vorlauf.py",
    "ai_trading_bot/core/engine/absendung_abgang.py",
    "ai_trading_bot/core/engine/order_aufbau.py",
    "ai_trading_bot/core/engine/verdraengung.py",
    "ai_trading_bot/core/engine/hitl_freigabe.py",
    "ai_trading_bot/core/engine/mandanten_zugang.py",
    "ai_trading_bot/core/engine/bemessung_helfer.py",
    "ai_trading_bot/core/engine/pm_zustand.py",
    "ai_trading_bot/core/engine/signal_desktop_entscheid.py",
    "ai_trading_bot/core/engine/signal_desktop_absendung.py",
    "ai_trading_bot/core/engine/ausfuehrung.py",
    "ai_trading_bot/core/gateway/order_gateway.py",
    "ai_trading_bot/core/gateway/fabrik.py",
    "ai_trading_bot/core/gateway/liquidation.py",
    "ai_trading_bot/core/outbox.py",
    "ai_trading_bot/core/lease.py",
    "ai_trading_bot/core/reconciliation.py",
    "ai_trading_bot/core/governance/iron_dome_policy.py",
    "ai_trading_bot/config.py",
    "ai_trading_bot/config.oss.py",
)


def _regeln(text: str) -> list[tuple[str, list[str]]]:
    regeln = []
    for zeile in text.splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if not zeile:
            continue
        muster, *besitzer = zeile.split()
        regeln.append((muster, besitzer))
    return regeln


def _passt(muster: str, pfad: str) -> bool:
    """CODEOWNERS-Muster (gitignore-Form) gegen einen repo-relativen Pfad."""
    verankert = muster.startswith("/")
    m = muster.lstrip("/")
    if m.endswith("/"):
        return pfad.startswith(m) if verankert else f"/{m}" in f"/{pfad}"
    if verankert or "/" in m:
        return fnmatch.fnmatch(pfad, m) or pfad.startswith(m.rstrip("*") + "/")
    return fnmatch.fnmatch(pfad.rsplit("/", 1)[-1], m)


def besitzer_von(text: str, pfad: str) -> list[str]:
    """Wie GitHub: die LETZTE passende Regel gewinnt."""
    treffer = [b for m, b in _regeln(text) if _passt(m, pfad)]
    return treffer[-1] if treffer else []


def test_der_abgleich_erkennt_eine_datei_ohne_eintrag():
    text = "/.github/ @owner\n/ai_trading_bot/core/compliance.py @owner\n"
    assert besitzer_von(text, "ai_trading_bot/core/compliance.py") == ["@owner"]
    assert besitzer_von(text, "ai_trading_bot/core/risk_manager.py") == []


def test_die_letzte_passende_regel_gewinnt():
    text = "/ai_trading_bot/core/ @a\n/ai_trading_bot/core/lease.py\n"
    assert besitzer_von(text, "ai_trading_bot/core/lease.py") == []
    assert besitzer_von(text, "ai_trading_bot/core/outbox.py") == ["@a"]


def test_jede_kapitalschutz_datei_existiert():
    fehlend = [p for p in KAPITALSCHUTZ if not (REPO / p).exists()]
    assert (
        not fehlend
    ), f"Die Kapitalschutz-Liste nennt Dateien, die es nicht gibt: {fehlend}"


@pytest.mark.parametrize("pfad", KAPITALSCHUTZ)
def test_jede_kapitalschutz_datei_hat_einen_menschlichen_code_owner(pfad):
    text = CODEOWNERS.read_text(encoding="utf-8")
    besitzer = besitzer_von(text, pfad)
    assert besitzer, f"{pfad} steht in keiner Zeile von .github/CODEOWNERS."
    assert all(b.startswith("@") for b in besitzer)


# ── #4431 Teil 1c: die Pfad-Regel und CODEOWNERS nennen dieselben Dateien ──────

KAPITALSCHUTZ_REGEL = (
    REPO / "docs" / "5_engineering_and_devops" / "pfad_regeln" / "kapitalschutz.toml"
)
# Sammeleintraege, die jede Datei darunter treffen. Sie zaehlen nicht als ausdruecklicher
# Eintrag: ohne eigene Zeile faellt eine Datei beim Umzug unbemerkt aus der Liste.
_SAMMELEINTRAEGE = {"/ai_trading_bot/"}


def _regel_dateien() -> list[str]:
    import re

    text = KAPITALSCHUTZ_REGEL.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"ai_trading_bot/core/[\w/]+\.py", text)))


def test_jede_datei_der_pfad_regel_steht_in_der_kapitalschutz_liste():
    fehlend = [p for p in _regel_dateien() if p not in KAPITALSCHUTZ]
    assert not fehlend, (
        "kapitalschutz.toml nennt Dateien, die KAPITALSCHUTZ nicht kennt: " f"{fehlend}"
    )


@pytest.mark.parametrize("pfad", KAPITALSCHUTZ)
def test_jede_kapitalschutz_datei_hat_einen_ausdruecklichen_eintrag(pfad):
    text = CODEOWNERS.read_text(encoding="utf-8")
    eigene = [
        m for m, _ in _regeln(text) if m not in _SAMMELEINTRAEGE and _passt(m, pfad)
    ]
    assert (
        eigene
    ), f"{pfad} ist in .github/CODEOWNERS nur ueber einen Sammeleintrag erfasst."


# ── Der Order-Deckel und die ratifizierte Obergrenze ─────────────────────────


@pytest.fixture
def order_deckel(monkeypatch):
    import config

    def setzen(wert):
        monkeypatch.setattr(config.get_config(), "COMPLIANCE_MAX_ORDER_VALUE", wert)

    return setzen


def test_ein_deckel_ueber_der_obergrenze_wird_nicht_uebernommen(order_deckel, caplog):
    from core.compliance import ComplianceGuardian
    from core.governance.iron_dome_policy import MAX_ORDER_VALUE_CEILING

    order_deckel(5_000_000.0)
    with caplog.at_level(logging.ERROR):
        guardian = ComplianceGuardian()

    assert guardian.max_order_value == MAX_ORDER_VALUE_CEILING
    assert any(
        "COMPLIANCE_MAX_ORDER_VALUE" in r.getMessage() and r.levelno >= logging.ERROR
        for r in caplog.records
    ), "Die Abweichung muss als Verstoss protokolliert werden."


def test_ein_deckel_unter_der_obergrenze_bleibt_einstellbar(order_deckel):
    """Owner-Entscheid 05.09.2026 (#3220): das Betriebslimit ist kundenseitig setzbar."""
    from core.compliance import ComplianceGuardian

    order_deckel(25_000.0)
    assert ComplianceGuardian().max_order_value == 25_000.0


def test_der_default_bleibt_unveraendert(order_deckel):
    from core.compliance import ComplianceGuardian

    order_deckel(10_000.0)
    assert ComplianceGuardian().max_order_value == 10_000.0
