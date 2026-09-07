"""Die Hash-Kette wird aus dem DATEIENDE gelesen, nicht aus der ganzen Datei.

**Vorfall 19./20.08.** `LocalJSONAuditLogger.__init__` ruft `_recover_last_hash()`, und das las die
neueste Tages-Logdatei per `read_text()` **vollstaendig** in den Speicher — erst als eine
Zeichenkette, dann als Liste aller Zeilen. Bei einer 2,59-GB-Datei
(`oss_audit_logs/audit_log_2026-08-19.jsonl`, entstanden weil alle Sim-Laeufe in dasselbe
Verzeichnis schreiben) sind das mehrere GB, nur um die **letzte** Hash-Zeile zu finden.

Folge: `MemoryError` im Konstruktor. Drei Sweep-Laeufe starben daran ohne Traceback (Haltedauer
Variante C, Risk-off, beide DrawdownGuard-Zellen — siehe
`docs/2956-drawdown-guard-posture/RESULTS.md`). Der Docstring der Methode versprach dabei
ausdruecklich *„read-only and never blocks or raises"* — `MemoryError` ist aber kein `OSError` und
lief durch.

Derselbe Konstruktor laeuft im Produktivpfad der WORM-Kette: ein Engine-Start mit grossem
Tages-Audit-Log traf denselben Fehler.

Diese Suite pinnt beides: die Leseoperation ist **beschraenkt**, und das Zusagen-Verhalten der
Methode bleibt unveraendert (Kette wird korrekt fortgesetzt, fail-safe auf Null-Hash).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.round_table.senate_log import LocalJSONAuditLogger


@pytest.fixture
def temp_senate_dir(tmp_path, monkeypatch):
    log_dir = tmp_path / "oss_audit_logs"
    monkeypatch.setenv("SENATE_LOG_DIR", str(log_dir))
    return log_dir


def _write(log_dir: Path, date_str: str, text: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    f = log_dir / f"audit_log_{date_str}.jsonl"
    f.write_text(text, encoding="utf-8")
    return f


class TestReadIsBounded:
    def test_whole_file_is_never_read(self, temp_senate_dir, monkeypatch):
        """Der Kern: `read_text()` darf auf einer Logdatei nicht mehr aufgerufen werden.

        Das ist die Zusicherung, die den Vorfall verhindert — nicht die Dateigroesse selbst.
        """
        _write(
            temp_senate_dir,
            "2026-08-19",
            "\n".join(json.dumps({"hash": f"{i:064d}"}) for i in range(500)) + "\n",
        )

        def _boom(self, *a, **kw):  # noqa: ANN001
            raise AssertionError(
                f"read_text() auf {self.name} — die ganze Datei wird wieder gelesen"
            )

        monkeypatch.setattr(Path, "read_text", _boom)
        assert LocalJSONAuditLogger()._last_hash == f"{499:064d}"

    def test_large_file_recovers_the_last_hash(self, temp_senate_dir):
        """Eine Datei deutlich groesser als jedes sinnvolle Lesefenster (~4 MB) muss trotzdem
        die LETZTE Hash-Zeile liefern."""
        filler = json.dumps({"hash": "a" * 64, "pad": "x" * 900})
        lines = [filler] * 4000 + [json.dumps({"hash": "b" * 64})]
        f = _write(temp_senate_dir, "2026-08-19", "\n".join(lines) + "\n")
        assert f.stat().st_size > 3_000_000, "Testdatei zu klein fuer die Aussage"
        assert LocalJSONAuditLogger()._last_hash == "b" * 64

    def test_memory_error_never_escapes(self, temp_senate_dir, monkeypatch):
        """Die Methode verspricht „never raises". `MemoryError` ist kein `OSError` und lief
        vorher durch — genau das hat die Laeufe getoetet."""
        _write(temp_senate_dir, "2026-08-19", json.dumps({"hash": "c" * 64}) + "\n")

        real_open = Path.open

        def _oom(self, *a, **kw):  # noqa: ANN001
            if self.name.startswith("audit_log_"):
                raise MemoryError("simuliert")
            return real_open(self, *a, **kw)

        monkeypatch.setattr(Path, "open", _oom)
        assert LocalJSONAuditLogger()._last_hash == "0" * 64


class TestTailWindowEdges:
    def test_partial_first_line_is_not_misparsed(self, temp_senate_dir):
        """Das Lesefenster schneidet mitten in eine Zeile. Diese angeschnittene erste Zeile darf
        nicht als Datensatz gelten — sonst waere ein halbes JSON ein „gefundener" Hash.
        """
        good = json.dumps({"hash": "d" * 64})
        _write(temp_senate_dir, "2026-08-19", "x" * 500_000 + "\n" + good + "\n")
        assert LocalJSONAuditLogger()._last_hash == "d" * 64

    def test_multibyte_char_at_the_window_boundary(self, temp_senate_dir):
        """Ein Byte-Fenster kann ein UTF-8-Zeichen zerschneiden. Das darf keinen Decode-Fehler
        werfen und die Kette nicht verlieren."""
        pad = json.dumps({"hash": "e" * 64, "note": "ä" * 100_000})
        _write(
            temp_senate_dir,
            "2026-08-19",
            pad + "\n" + json.dumps({"hash": "f" * 64}) + "\n",
        )
        assert LocalJSONAuditLogger()._last_hash == "f" * 64

    def test_trailing_newlines_and_blank_lines(self, temp_senate_dir):
        _write(
            temp_senate_dir,
            "2026-08-19",
            json.dumps({"hash": "9" * 64}) + "\n\n\n   \n",
        )
        assert LocalJSONAuditLogger()._last_hash == "9" * 64


class TestUnchangedContract:
    """Das dokumentierte Verhalten bleibt gleich — der Fix ist eine Lese-Optimierung, keine
    Semantik-Aenderung."""

    def test_resumes_from_the_newest_file(self, temp_senate_dir):
        _write(temp_senate_dir, "2026-08-18", json.dumps({"hash": "1" * 64}) + "\n")
        _write(temp_senate_dir, "2026-08-19", json.dumps({"hash": "2" * 64}) + "\n")
        assert LocalJSONAuditLogger()._last_hash == "2" * 64

    def test_falls_back_to_the_previous_file_when_newest_is_empty(
        self, temp_senate_dir
    ):
        _write(temp_senate_dir, "2026-08-18", json.dumps({"hash": "3" * 64}) + "\n")
        _write(temp_senate_dir, "2026-08-19", "")
        assert LocalJSONAuditLogger()._last_hash == "3" * 64

    def test_no_files_is_a_fresh_chain(self, temp_senate_dir):
        temp_senate_dir.mkdir(parents=True, exist_ok=True)
        assert LocalJSONAuditLogger()._last_hash == "0" * 64

    def test_corrupt_lines_are_skipped_not_fatal(self, temp_senate_dir):
        _write(
            temp_senate_dir,
            "2026-08-19",
            json.dumps({"hash": "7" * 64})
            + "\n{ kein json\n"
            + '{"hash": "zu-kurz"}\n',
        )
        assert LocalJSONAuditLogger()._last_hash == "7" * 64

    def test_hash_must_be_64_chars(self, temp_senate_dir):
        _write(temp_senate_dir, "2026-08-19", json.dumps({"hash": "abc"}) + "\n")
        assert LocalJSONAuditLogger()._last_hash == "0" * 64
