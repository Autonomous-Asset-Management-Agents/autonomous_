"""#1883 — die „Dynamic Constitution" ist entfernt; hier steht die Sperre dagegen.

**Was hier vorher stand.** Ein Test für `AILearningEngine.update_dynamic_agent_weights`:
Vertrauenspunkte aus Redis lesen, auf die Agentengewichte addieren, klemmen,
zurückschreiben. Die Funktion gibt es nicht mehr.

**Warum sie weg ist** (Entscheid 28.09.2026, gemessen am Code):

* Der Kreis war bis auf ein Glied geschlossen — `record_entry` hat genau einen
  Aufrufer (`core/strategies/rl_execution.py`, der Live-Pfad), und der übergab die
  Round-Table-Stimmen nie. Seit Juli stand also jedes Gewicht auf seiner Vorgabe.
* Die fehlende Zeile einzufügen hätte es schlimmer gemacht, nicht besser: Ein
  Vertrauenspunkt war **1,0 Gewicht**, bei Agentengrenzen von 0,15–1,50 bzw.
  0,20–2,00. **Ein einziger geschlossener Trade** hätte einen Agenten vom Standard
  (~0,4) an den Deckel oder auf den Boden geschoben — bei roher P/L-Zuordnung ohne
  Kontrolle für die Marktrichtung, ohne Mindestbeobachtungszahl, ohne Signifikanztest.
* Der fundierte Nachfolger ist Epic #3702 (RLW), gestützt auf die RTR-0-Messung mit
  FDR-Signifikanz.

**Was bleibt:** der *Leser* `base_agent.weight`. Er ist der Einstiegspunkt für #3702
und trägt den Sicherheits-Wächter aus #942, der fremde Redis-Werte außerhalb der
Klassengrenzen meldet.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

pytestmark = pytest.mark.vc1

#: Dateien, die den Schlüssel nennen DÜRFEN — und warum.
ERLAUBT = {
    "core/round_table/base_agent.py",  # der Leser (#3702-Einstieg, Waechter #942)
    "core/local_state_client.py",  # die Hash-Primitive, nennt ihn nur im Kommentar
}


def _fundstellen() -> dict[str, list[str]]:
    """Alle Produktivstellen, die ``agent_weights_v2`` nennen, je Datei."""
    treffer: dict[str, list[str]] = {}
    for pfad in (_AI_BOT / "core").rglob("*.py"):
        text = pfad.read_text(encoding="utf-8", errors="replace")
        if "agent_weights_v2" not in text:
            continue
        rel = pfad.relative_to(_AI_BOT).as_posix()
        treffer[rel] = [z for z in text.splitlines() if "agent_weights_v2" in z]
    return treffer


def test_kein_produktiver_schreiber_auf_agent_weights_v2():
    """Die Sperre: Wer die Gewichte wieder automatisch schreiben will, kommt hier vorbei.

    Ein Schreiber ohne Signifikanzprüfung wäre die Rückkehr genau des Verstärkers, der
    mit #1883 entfernt wurde.
    """
    fremde = {d: z for d, z in _fundstellen().items() if d not in ERLAUBT}
    assert not fremde, (
        "Neue Fundstelle(n) für agent_weights_v2 ausserhalb des Lesers:\n"
        + "\n".join(f"  {d}: {zeilen}" for d, zeilen in fremde.items())
        + "\nGelernte Gewichte gehoeren nach Epic #3702 (RLW), mit Signifikanz-Nachweis."
    )

    for datei, zeilen in _fundstellen().items():
        for zeile in zeilen:
            assert (
                "hset" not in zeile and "hmset" not in zeile
            ), f"{datei}: schreibender Zugriff auf agent_weights_v2 — {zeile.strip()}"


def test_die_lernschleife_ist_fort():
    """Beide Glieder der Rueckkopplung sind entfernt."""
    from core.learning.engine import AILearningEngine
    from core.trade_intelligence import TradeIntelligence

    assert not hasattr(AILearningEngine, "update_dynamic_agent_weights")
    assert not hasattr(TradeIntelligence, "_attribute_trade_to_agents")


def test_der_leser_bleibt_erhalten():
    """`base_agent.weight` ist der Einstiegspunkt fuer #3702 — er darf NICHT mitentfernt
    werden, sonst faellt auch der Waechter aus #942 weg."""
    quelle = (_AI_BOT / "core" / "round_table" / "base_agent.py").read_text(
        encoding="utf-8"
    )
    assert "agent_weights_v2" in quelle
    assert "rogue agent weight manipulation" in quelle
