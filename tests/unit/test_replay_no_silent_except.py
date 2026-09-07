# tests/unit/test_replay_no_silent_except.py
"""#3228 Phase 2, Review-Befund POLICY-01 (P0) — kein stilles ``except`` im Replay.

Der Review zu PR #3233 hat vier ``except Exception: pass`` in
``core/analysis/attribution/replay.py`` als PR-blockierend markiert. Alle vier
liegen in **Aufräum- und Wiederherstellungspfaden**, und genau das macht sie
gefährlich — ein stiller Fehlschlag lässt den Prozess in einem falschen Zustand
zurück, ohne dass irgendwo etwas davon steht:

* das Setzen der Replay-Uhr — schlägt es fehl, liest der LSTM-Agent den
  Panel-Snapshot eines **anderen** Datums weiter: stiller Look-ahead;
* das Zurücksetzen von ``SIM_MODE`` und der Sim-Uhr — schlägt es fehl, wirkt der
  Simulationszustand in nachfolgende Läufe hinein;
* das Zurücksetzen des Panel-Store-Aufbewahrungsfensters — schlägt es fehl,
  behält der Store das für den Replay hochgesetzte Fenster.

In allen vier Fällen ist die Fortsetzung richtig (ein Aufräumfehler darf den
Replay nicht abbrechen) — aber sie muss laut sein. CLAUDE.md §5.6.

Der Test prüft die Quelle per AST, weil das Verhalten strukturell ist: ein
verschluckter Aufräumfehler fällt zur Laufzeit erst auf, wenn ein späterer Lauf
unerklärliche Ergebnisse liefert.
"""

import ast
import io
import os

import allure

_REPLAY = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "core",
    "analysis",
    "attribution",
    "replay.py",
)


def _silent_handlers(tree: ast.AST) -> list:
    """(Zeile, Quelltext) je ``except``-Block ohne jedes Logging."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        logs = False
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            f = inner.func
            if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                if f.value.id in {"logger", "logging"}:
                    logs = True
                    break
        # Ein ``raise`` oder ein ``return`` ist ebenfalls in Ordnung: dort geht der
        # Fehlschlag nicht verloren, sondern wird dem Aufrufer mitgeteilt — etwa
        # ``except (TypeError, ValueError): return None`` als "dieser Rahmen ist
        # unbrauchbar, überspringen". Problematisch ist nur das stille Durchfallen,
        # nach dem der Ablauf so weiterläuft, als wäre nichts gewesen.
        signals = any(isinstance(x, (ast.Raise, ast.Return)) for x in ast.walk(node))
        if not logs and not signals:
            out.append(node.lineno)
    return out


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestReplayHasNoSilentExcept:
    def test_every_except_block_is_audible(self):
        """Jeder gefangene Fehler wird geloggt oder weitergereicht — keiner verschwindet."""
        src = io.open(_REPLAY, encoding="utf-8").read()
        silent = _silent_handlers(ast.parse(src))

        assert not silent, (
            f"replay.py verschluckt Fehler in Zeile(n) {silent} ohne jedes Log. "
            "Aufräumpfade dürfen fortsetzen, aber nicht schweigen — ein fehlgeschlagenes "
            "Restore lässt den Prozess in einem falschen Zustand zurück (§5.6)."
        )
