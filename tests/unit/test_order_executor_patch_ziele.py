"""Kein Patch auf ``core.engine.order_executor`` darf ins Leere laufen (H-1a, #4230).

Vor dem Schnitt von ``order_executor.py`` (H-1b … H-1l, Entscheidung #4183) patchen die Tests
Namen am Kern: ``core.engine.order_executor.RedisClient``, ``.config``, ``.kill_switch`` und
andere. Zieht der Leser eines solchen Namens in ein eigenes Modul und bindet ihn dort per
Import an sich, greift der Patch nicht mehr: laut, wo ``patch()`` den Namen nicht findet,
still, wo der Test trotzdem gruen bleibt.

Regel (Entscheidung §3, Weg b): Was im Kern gebunden bleibt, bleibt am Kern patchbar. Jedes
ausgezogene Modul liest es zur Laufzeit als ``order_executor.<name>``, wie heute
``signal_uebergabe.py`` und ``absendung_nachlauf.py``. Zwei Befunde:

(a) **Patch ins Leere:** Ein gepatchter Name wird weder im Kern frei gelesen noch in einem
    Modul des Pakets als ``order_executor.<name>``.
(b) **Patch am Leser vorbei:** Ein Modul des Pakets importiert den Kern auf Modulebene und
    benutzt einen gepatchten Namen frei.

Modul, Kern und Paket sind Parameter; H-2 (#4184) tauscht nur diese drei aus. Muster:
``test_specialist_patch_ziele.py`` (G-8a2), ``test_routes_patch_ziele.py`` (G-4).
"""

from __future__ import annotations

import ast
import re
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

PAKET = Path(__file__).resolve().parents[2]  # ai_trading_bot/
ENGINE = PAKET / "core" / "engine"
KERN = ENGINE / "order_executor.py"
TESTS = PAKET / "tests"

# Entscheidung #4183 §3: die 24 gepatchten Namen. ``_derived_coid`` steht dort mit einer
# String-Stelle; in ``tests/`` kommt er aber nur als Import und in einem Docstring vor
# (``tests/chain/_ein_intent.py:16``), nie als Patch. Ebenso ist die String-Stelle von
# ``OrderExecutorMixin`` ein Docstring (``_ein_intent.py:18``); die Tests ersetzen nur
# Attribute *an* der Klasse (``patch.object(oe.OrderExecutorMixin, …)``), nicht ihre Bindung
# am Kern. Der Waechter fuehrt nur, was er am Code nachweist — beide Abweichungen stehen im
# Walkthrough, die Entscheidung bleibt, wie sie ist.
ENTSCHEIDUNG_NAMEN = {
    "RedisClient",
    "config",
    "restore_pm_state_from_redis",
    "kill_switch",
    "persist_pm_state_to_redis",
    "_record_gateway_decision",
    "USER_SECRETS_AVAILABLE",
    "tracer",
    "logging",
    "_rec_outcome",
    "_regime_throttled_size",
    "create_trading_client",
    "gateway_for",
    "_dust_floor_skips_exit",
    "erfasse_order_endzustand",
    "_audit_skipped_signal",
    "_earnings_guard_veto",
    "_capture_outcome",
    "_bump_exec",
}
# ``baue_trade_record`` stand hier bis H-1b (#4231). Sein Leser ``erfasse_order_endzustand``
# zog mit nach ``order_aufbau.py``; der Test patcht seither dort (Entscheidung §3, Weg a)
# und ohne ``raising=False`` — ein Patch am Kern liefe ins Leere, und genau das meldete
# dieser Waechter.
# ``datetime`` stand hier bis H-1c (#4232). Sein Leser ``restore_pm_state_from_redis`` zog
# nach ``pm_zustand.py``; ``FixedClock`` (``tests/helpers/stubs.py``) patcht seither dort
# (Entscheidung §3, Weg a). Der Kern importiert ``datetime`` nicht mehr.
# ``_OUTBOX_OHNE_ABLAGE_GEMELDET`` stand hier bis H-1h (#4237). Sein Schreiber
# ``_outbox_sitzung`` zog nach ``absendung_tor.py``; ``test_3449_outbox_verdrahtung.py``
# setzt den Zustand seither dort (Entscheidung §3, Weg a). Der Kern führt keine Kopie.

# Befunde, die auf main bereits stehen (gemessen 06.10.2026). Beobachtung, nicht Soll:
# ``signal_uebergabe.py`` (G-1a) bindet beide Namen per ``from .order_executor import …``
# auf Modulebene. Die Tests, die sie am Kern patchen, fahren heute nur den Mandantenpfad
# im Kern (``test_3821_absendung_schritte.py``, ``test_3821_absendung_charakterisierung.py``)
# — dort greift der Patch. Den Desktop-Pfad in ``signal_uebergabe.py`` erreicht er nicht.
# Die Korrektur ist Produktivcode und gehoert in ein eigenes Issue (Epic §5), nicht in H-1a.
# Wer sie behebt, streicht die Zeile hier mit; jeder NEUE Befund macht den Waechter rot.
# ``mandanten_zugang.py`` (H-1d, #4233): ``get_active_tenant_clients`` importiert
# ``create_trading_client`` funktionslokal aus ``core.client_factory``. Schon im Kern
# ueberschattete dieser Import den Modulnamen, ein Patch am Kern traf die Methode also nie
# (Modul-Docstring von ``test_h1a_mandanten_zugang_charakterisierung.py``). Der Plan laesst
# den Import wortgleich; ihn auf ``order_executor.create_trading_client`` umzustellen,
# aenderte, welche Patches greifen, und gehoert in ein eigenes Issue.
BEKANNTE_BEFUNDE = {
    ("signal_uebergabe.py", "_audit_skipped_signal"),
    ("signal_uebergabe.py", "_dust_floor_skips_exit"),
    ("mandanten_zugang.py", "create_trading_client"),
}


def gepatchte_namen(
    tests: Path, modul: str = "core.engine.order_executor"
) -> dict[str, set[str]]:
    """Name → Testdateien, die ihn ueber das Modul ``modul`` patchen.

    Gezaehlt wird das erste Segment nach dem Modul: ``"<modul>.config.get_config"`` patcht
    ``config``. Dazu ``patch.object``/``(monkeypatch.)setattr`` auf jeden Alias des Moduls.
    Nicht gezaehlt: ``patch.object(oe.X, "attr")`` — das ersetzt ein Attribut am Objekt
    ``X`` selbst, und das ist dasselbe Objekt, egal ueber welche Bindung ein Leser es holt.
    """
    paket, _, kurz = modul.rpartition(".")
    pfad = re.compile(rf"[\"']{re.escape(modul)}\.([A-Za-z_]\w*)[\w.]*[\"']")
    alias = re.compile(
        rf"import\s+{re.escape(modul)}\s+as\s+(\w+)"
        rf"|from\s+{re.escape(paket)}\s+import\s+{re.escape(kurz)}\b(?:\s+as\s+(\w+))?"
    )
    ergebnis: dict[str, set[str]] = {}
    for datei in tests.rglob("*.py"):
        if datei.resolve() == Path(__file__).resolve():
            continue  # die Beispiel-Quellen dieses Waechters sind keine Patches
        text = datei.read_text(encoding="utf-8", errors="replace")
        namen = set(pfad.findall(text))
        for m in alias.finditer(text):
            name = m.group(1) or m.group(2) or kurz
            namen |= set(
                re.findall(
                    rf"(?:patch\.object|setattr)\(\s*{re.escape(name)}\s*,"
                    rf"\s*[\"']([A-Za-z_]\w*)[\"']",
                    text,
                )
            )
        for name in namen:
            ergebnis.setdefault(name, set()).add(datei.name)
    return ergebnis


def _importiert_kern(baum: ast.Module, kernname: str) -> bool:
    """Bindet das Modul den Kern auf Modulebene unter ``kernname`` (komponiertes Modul)?"""
    for knoten in baum.body:
        if isinstance(knoten, (ast.Import, ast.ImportFrom)):
            for a in knoten.names:
                if (a.asname or a.name).rpartition(".")[2] == kernname:
                    return True
    return False


def pruefe(
    kern: Path,
    paket: Path,
    gepatcht: dict[str, set[str]],
    kernname: str = "order_executor",
) -> list[str]:
    meldungen = []
    gelesen = {
        x.id
        for x in ast.walk(ast.parse(kern.read_text(encoding="utf-8")))
        if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
    }
    for datei in sorted(paket.glob("*.py")):
        if datei.resolve() == kern.resolve():
            continue
        baum = ast.parse(datei.read_text(encoding="utf-8"), filename=str(datei))
        frei: dict[str, int] = {}
        for x in ast.walk(baum):
            if (
                isinstance(x, ast.Attribute)
                and isinstance(x.value, ast.Name)
                and x.value.id == kernname
            ):
                gelesen.add(x.attr)
            elif isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                frei.setdefault(x.id, x.lineno)
        if not _importiert_kern(baum, kernname):
            continue
        for name in sorted(set(frei) & set(gepatcht)):
            meldungen.append(
                f"{datei.name}:{frei[name]}: '{name}' wird frei benutzt, Tests patchen "
                f"aber {kernname}.{name} ({', '.join(sorted(gepatcht[name]))}). "
                f"Lies es zur Laufzeit als '{kernname}.{name}'."
            )
    for name in sorted(set(gepatcht) - gelesen):
        meldungen.append(
            f"{kern.name}: '{name}' wird gepatcht ({', '.join(sorted(gepatcht[name]))}), "
            f"aber weder im Kern frei gelesen noch irgendwo als '{kernname}.{name}' — "
            "der Patch laeuft ins Leere."
        )
    return meldungen


def _schreibe(wurzel: Path, name: str, quelle: str) -> Path:
    datei = wurzel / name
    datei.parent.mkdir(parents=True, exist_ok=True)
    datei.write_text(textwrap.dedent(quelle), encoding="utf-8")
    return datei


def _probe_kern(wurzel: Path) -> Path:
    return _schreibe(
        wurzel,
        "engine/order_executor.py",
        """\
        from core.redis_client import RedisClient
        from core.kill_switch import kill_switch

        def f():
            return RedisClient.get_redis()
        """,
    )


# ── Schritt 1: Probe-Faelle ───────────────────────────────────────────────────


def test_patch_ins_leere_ist_ein_befund(tmp_path):
    kern = _probe_kern(tmp_path)
    _schreibe(tmp_path, "engine/verdraengung.py", "def g():\n    return 1\n")
    (meldung,) = pruefe(kern, tmp_path / "engine", {"kill_switch": {"test_a.py"}})
    assert "order_executor.py" in meldung
    assert "'kill_switch'" in meldung and "test_a.py" in meldung


def test_leser_ueber_kernname_ist_erlaubt(tmp_path):
    kern = _probe_kern(tmp_path)
    _schreibe(
        tmp_path,
        "engine/verdraengung.py",
        """\
        from . import order_executor

        def g():
            return order_executor.RedisClient, order_executor.kill_switch
        """,
    )
    gepatcht = {"RedisClient": {"t.py"}, "kill_switch": {"t.py"}}
    assert pruefe(kern, tmp_path / "engine", gepatcht) == []


def test_freier_name_im_komponierten_modul_ist_ein_befund(tmp_path):
    kern = _probe_kern(tmp_path)
    _schreibe(
        tmp_path,
        "engine/verdraengung.py",
        """\
        from core.redis_client import RedisClient

        from . import order_executor

        def g():
            return RedisClient.get_redis()
        """,
    )
    (meldung,) = pruefe(kern, tmp_path / "engine", {"RedisClient": {"t.py"}})
    assert "verdraengung.py:6" in meldung and "order_executor.RedisClient" in meldung


def test_gepatchte_namen_erkennt_alle_formen(tmp_path):
    _schreibe(
        tmp_path,
        "test_x.py",
        """\
        import core.engine.order_executor as oe
        from core.engine import order_executor
        patch("core.engine.order_executor.RedisClient")
        patch("core.engine.order_executor.config.get_config")
        patch.object(oe, "_record_gateway_decision", f)
        monkeypatch.setattr(order_executor, "gateway_for", g)
        setattr(oe, "_bump_exec", h)
        patch.object(oe.OrderExecutorMixin, "_sende_durchs_tor", AsyncMock())
        patch("core.engine.order_executor_nachbar.nicht_gemeint")
        """,
    )
    assert set(gepatchte_namen(tmp_path)) == {
        "RedisClient",
        "config",
        "_record_gateway_decision",
        "gateway_for",
        "_bump_exec",
    }


def test_modul_und_pfade_sind_parameter(tmp_path):
    """Vorgriff H-2 (#4184): derselbe Pruefer fuer ``core.engine.trading_loop``."""
    _schreibe(
        tmp_path,
        "tests/test_t.py",
        """\
        from core.engine import trading_loop as tl
        patch("core.engine.trading_loop.RedisClient")
        patch.object(tl, "verschwunden", f)
        """,
    )
    gepatcht = gepatchte_namen(tmp_path / "tests", modul="core.engine.trading_loop")
    assert set(gepatcht) == {"RedisClient", "verschwunden"}
    kern = _schreibe(
        tmp_path, "engine/trading_loop.py", "def f():\n    return RedisClient\n"
    )
    _schreibe(
        tmp_path,
        "engine/schleife_teil.py",
        """\
        from . import trading_loop

        def g():
            return RedisClient
        """,
    )
    meldungen = pruefe(kern, tmp_path / "engine", gepatcht, kernname="trading_loop")
    assert len(meldungen) == 2
    assert any("'verschwunden'" in m for m in meldungen)
    assert any(
        "schleife_teil.py:4" in m and "trading_loop.RedisClient" in m for m in meldungen
    )


# ── Schritt 2: gegen den Code ─────────────────────────────────────────────────


def test_patch_ziele_gegen_den_code():
    gepatcht = gepatchte_namen(TESTS)
    # Die Messung der Entscheidung muss der Waechter selbst sehen, sonst prueft er nichts.
    fehlend = ENTSCHEIDUNG_NAMEN - set(gepatcht)
    assert not fehlend, f"Waechter sieht diese Patches nicht: {sorted(fehlend)}"
    meldungen = pruefe(KERN, ENGINE, gepatcht)
    schluessel = {
        x: re.match(r"([\w.]+?)(?::\d+)?: '(\w+)'", x).groups() for x in meldungen
    }
    neu = [x for x, k in schluessel.items() if k not in BEKANNTE_BEFUNDE]
    assert not neu, "\n".join(neu)
    gefunden = set(schluessel.values())
    # Ist ein bekannter Befund behoben, faellt er hier auf: dann die Liste kuerzen.
    assert gefunden == BEKANNTE_BEFUNDE
