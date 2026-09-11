# tests/unit/test_runner_exception_logging.py
"""POLICY-01 — Fehler-Logging in ``run_round_table`` darf den Stack-Trace nicht verlieren.

Ausgeloest durch den Review zu PR #3223 (Fundstelle ``runner.py`` im
Coverage-Zweig aus #3210). Die Pruefung zeigte ein Muster: von zehn
``except``-Bloecken mit Logging trug genau einer den Trace — Zeile 298,
und zwar seit demselben Review-Punkt in #2418:

    # #2418 review P2: exc_info=True for the stack trace; WARNING kept (§5.6).
    logger.warning("MetaLabel gate error for %s: %s", symbol, exc, exc_info=True)

Diese Hausloesung wird hier verallgemeinert und festgeschrieben. Wichtig ist
die Nuance: ``logger.exception`` loggt auf ERROR. Ein fail-safe
Beobachtungspfad, der bewusst zum No-op degradiert, darf dadurch nicht zum
Alarm eskalieren — deshalb bleibt WARNING WARNING (CLAUDE.md §5.6 verlangt
nur, dass es nicht DEBUG ist) und bekommt ``exc_info=True``; ein bestehendes
``logger.error`` wird zu ``logger.exception`` (gleiche Schwere, plus Trace).

Der Test arbeitet auf der Quelle (AST), weil das Verhalten strukturell ist:
ein verlorener Trace faellt zur Laufzeit erst auf, wenn man ihn braucht.
"""

import ast
import io
import os

import allure

_RUNNER = os.path.join(
    os.path.dirname(__file__),
    "..",
    "..",
    "core",
    "round_table",
    "runner.py",
)

# Diese Level tragen den Trace nicht von selbst und brauchen exc_info.
_NEEDS_EXC_INFO = {"warning", "error", "info", "debug", "critical"}


def _logging_calls_in_except_blocks(tree):
    """Liefert (Zeile, Level, hat_exc_info) je logger-Aufruf in einem except-Block."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if not isinstance(func, ast.Attribute):
                continue
            target = func.value
            if not (isinstance(target, ast.Name) and target.id == "logger"):
                continue
            if func.attr not in _NEEDS_EXC_INFO and func.attr != "exception":
                continue
            has_exc_info = any(kw.arg == "exc_info" for kw in inner.keywords if kw.arg)
            found.append((inner.lineno, func.attr, has_exc_info))
    return found


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestRunnerExceptionLogging:
    def test_every_except_block_log_keeps_the_stack_trace(self):
        """Jeder logger-Aufruf in einem except-Block traegt den Trace."""
        src = io.open(_RUNNER, encoding="utf-8").read()
        calls = _logging_calls_in_except_blocks(ast.parse(src))

        assert calls, "keine except-Block-Logs gefunden — Test greift ins Leere"

        offenders = [
            (line, level)
            for line, level, has_exc_info in calls
            if level != "exception" and not has_exc_info
        ]
        assert not offenders, (
            "runner.py verliert den Stack-Trace in "
            f"{len(offenders)} except-Block(en): {offenders}. "
            "logger.exception(...) oder exc_info=True verwenden — "
            "WARNING bleibt WARNING (§5.6), siehe Zeile 298 (#2418)."
        )

    def test_warning_severity_is_not_escalated_to_error(self):
        """Gegenprobe: der Fix darf fail-safe-Pfade nicht zu ERROR eskalieren.

        ``logger.exception`` loggt auf ERROR. Waeren die bewusst als WARNING
        gefuehrten Beobachtungspfade pauschal darauf umgestellt, wuerde ein
        harmloser No-op-Fallback als Fehler alarmieren.
        """
        src = io.open(_RUNNER, encoding="utf-8").read()
        calls = _logging_calls_in_except_blocks(ast.parse(src))
        warnings = [c for c in calls if c[1] == "warning"]

        assert warnings, (
            "kein einziger WARNING-Pfad mehr in except-Bloecken — die "
            "Schweregrade wurden vermutlich pauschal auf exception() gehoben."
        )
