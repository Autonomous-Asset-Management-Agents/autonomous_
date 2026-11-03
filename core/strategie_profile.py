"""#4629 (STW-3, Epic #4625) — Strategien als benannte, WORM-versiegelte Settings-Saetze.

Eine Strategie ist ein Name plus eine Teilmenge der Werte aus
``core.trading_settings.REGISTRY``, in deren kanonischer Textform. Hoechstens
``MAX_STRATEGIEN`` davon liegen in ``strategien.json`` im Datenverzeichnis
(``AAA_USER_DATA_DIR``); der aktuelle Live-Satz bleibt in ``setup.json`` und zaehlt nicht mit.

**Pruefen statt klemmen.** ``trading_settings._parse`` klemmt still auf ``[lo, hi]`` bzw. faellt
auf den Default zurueck. Fuer eine Strategie waere das falsch: versiegelt wuerde ein Satz,
den der Nutzer nie angelegt hat. ``pruefe_strategie`` lehnt deshalb jeden Wert ausserhalb
der Registry-Grenzen mit ``UngueltigeEinstellungError`` ab (422 im Endpoint).

**Leitplanken.** Compliance-Leitplanken (``LEITPLANKEN_KEYS``) stehen nie in einer Strategie.
Sie stehen auch nicht in der Registry; die eigene Liste sorgt fuer eine Meldung, die sagt
warum, statt nur „unbekannt".

**Reihenfolge.** Die Ablage schreibt der Endpoint (``core/engine/routes/steuerung.py``) erst
NACH dem WORM-Eintrag (``hitl_gate.log_strategy_definition_event``). Die Engine besitzt diese
Datei — anders als ``setup.json`` (#2865); Plan §6 „Eigentuemerschaft der Datei".

Plan: ``docs/4629-*/implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core import trading_settings as ts
from core.audit_paths import resolve_audit_log_path

logger = logging.getLogger(__name__)

#: Obergrenze der benannten Strategien (Epic #4625: „bis zu drei eigene Strategien").
MAX_STRATEGIEN = 3
#: Hoechstlaenge eines Strategienamens (Anzeige, Dateiinhalt, Kettenfeld).
NAME_MAXLEN = 64
ABLAGE_DATEI = "strategien.json"

#: Compliance-Leitplanken (Modul-Docstring ``core/trading_settings.py``): nie Teil einer
#: Strategie. ``HARD_STOP_LOSS_PCT`` kommt aus ``ts._GUARDRAIL_VIEW_KEYS``; PDT/FINRA
#: steht in ``settings.py`` als ``GATEKEEPER_PDT_CROSSDAY_EXEMPTION``.
LEITPLANKEN_KEYS = frozenset(
    {k for k, _, _ in ts._GUARDRAIL_VIEW_KEYS}
    | {
        "HITL_ENABLED",
        "RISK_FORBID_LEVERAGE",
        "TRAILING_STOP_PCT",
        "GATEKEEPER_PDT_CROSSDAY_EXEMPTION",
    }
)

#: Serialisiert Anlegen, Aendern und Loeschen: Obergrenze und Ablage lesen und schreiben
#: dieselbe Datei, mit dem WORM-Eintrag dazwischen.
SPERRE = asyncio.Lock()


class StrategieAblageError(RuntimeError):
    """``strategien.json`` ist nicht lesbar — nie still als „leer" behandeln.

    #4890: Die Meldung nennt den Grund nicht; der steht in der WARNING-Zeile von
    ``lade_strategien``.
    """

    code = "strategy_store_unreadable"
    params: Dict[str, Any] = {}


@dataclass(frozen=True)
class Strategie:
    """Eine benannte Strategie: Registry-Keys in kanonischer Textform."""

    name: str
    werte: Dict[str, str]


# ── Request/Response — lokal, nicht in core/contracts/ (Plan §2 Punkt 1) ────────


class StrategyCreateRequest(BaseModel):
    name: str
    werte: Dict[str, Any] = Field(default_factory=dict)
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class StrategyDeleteRequest(BaseModel):
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class StrategyResponse(BaseModel):
    success: bool
    aktion: str
    name: str
    werte: Dict[str, str]
    detail: str
    # #4660: bei Quelle ``liste`` der Pruefgrund je verworfenem Symbol, sonst None.
    liste_verworfen: Optional[Dict[str, str]] = None


class StrategyListResponse(BaseModel):
    strategien: List[Dict[str, Any]]
    max_strategien: int


class StrategyAssignRequest(BaseModel):
    """#4630: Body von ``POST /api/strategies/{name}/assign-live``."""

    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class StrategyAssignPaperRequest(BaseModel):
    """#5123: Body von ``POST /api/strategies/{name}/assign-paper``.

    ``konto`` ist ein ``Literal`` ohne ``live``: Eine Live-Zuweisung ueber die Paper-Route
    scheitert schon hier mit 422 — vor Sperre, Nonce und Eintrag. Das ist die Bedingung der
    Owner-Bestaetigung an #5123 (kein HITL-Gate auf Paper). ``basis`` ist der Stand des
    Platzes, gegen den der Diff laeuft (Plan #4953 §2.1).
    """

    konto: Literal["paper1", "paper2", "paper3"]
    basis: Dict[str, str]
    acknowledgment: str = Field(min_length=1)
    nonce: str = Field(min_length=1)


class StrategyAssignResponse(BaseModel):
    """#4630: ``changes`` im Klartext — genau das schreibt die Shell nach ``setup.json``."""

    success: bool
    strategie: str
    applied: Dict[str, str]
    changes: List[Dict[str, str]]
    detail: str


# ── Pruefung ─────────────────────────────────────────────────────────────────


def _ungueltig(
    s: ts.Setting, wert: Any, grund: str, code: str, **params: Any
) -> ts.UngueltigeEinstellungError:
    """#4890: englische Meldung plus ``code``/``params`` (Katalog ``core/engine/fehler.py``)."""
    return ts.UngueltigeEinstellungError(
        f"{s.key}={wert!r}: {grund}",
        code=code,
        params={"key": s.key, "value": ts._wert_param(wert), **params},
    )


def _falscher_typ(s: ts.Setting, wert: Any, grund: str, expected: str):
    return _ungueltig(s, wert, grund, "setting_value_type", expected=expected)


def _pruefe_wert(s: ts.Setting, wert: Any) -> str:
    """Kanonischer Text fuer einen gueltigen Wert — sonst ``UngueltigeEinstellungError``."""
    if s.kind == "bool":
        if isinstance(wert, bool):
            return "true" if wert else "false"
        if isinstance(wert, str) and wert.strip().lower() in ("true", "false"):
            return wert.strip().lower()
        raise _falscher_typ(s, wert, "expected true or false", "boolean")
    if s.kind == "enum":
        v = wert.strip().lower() if isinstance(wert, str) else None
        if v not in s.choices:
            raise _ungueltig(
                s,
                wert,
                "allowed values: " + ", ".join(s.choices),
                "setting_value_not_allowed",
                allowed=list(s.choices),
            )
        return v
    if s.kind == "text":
        if not isinstance(wert, str):
            raise _falscher_typ(s, wert, "expected text", "text")
        laenge = len(wert.strip())
        if laenge > s.maxlen:
            # Der Text selbst (der Prompt) erscheint nie in der Antwort — nur seine Laenge.
            raise ts.UngueltigeEinstellungError(
                f"{s.key}: {laenge} characters exceeds the maximum of {s.maxlen}",
                code="setting_text_too_long",
                params={"key": s.key, "length": laenge, "max": s.maxlen},
            )
        return ts.canonical_text(s.key, wert)
    # int/float — bool ist in Python eine 1 und waere eine stumme Fehlinterpretation.
    if isinstance(wert, bool):
        raise _falscher_typ(s, wert, "expected a number", "number")
    try:
        v = float(wert)
    except (TypeError, ValueError):
        raise _falscher_typ(s, wert, "expected a number", "number") from None
    if not math.isfinite(v):
        raise _falscher_typ(s, wert, "expected a finite number", "finite_number")
    if s.kind == "int" and v != int(v):
        raise _falscher_typ(s, wert, "expected an integer", "integer")
    if not s.lo <= v <= s.hi:
        raise _ungueltig(
            s,
            wert,
            f"outside [{s.lo}, {s.hi}]",
            "setting_value_out_of_range",
            min=s.lo,
            max=s.hi,
        )
    return ts.canonical_text(s.key, v)


def pruefe_name(name: str) -> str:
    n = (name or "").strip()
    if not n or len(n) > NAME_MAXLEN:
        raise ts.UngueltigeEinstellungError(
            f"Strategy name must be 1 to {NAME_MAXLEN} characters long",
            code="strategy_name_length",
            params={"min": 1, "max": NAME_MAXLEN},
        )
    return n


def pruefe_strategie(name: str, werte: Dict[str, Any]) -> Dict[str, str]:
    """Deterministische Pruefung eines Satzes; Ergebnis kanonisch und nach Key sortiert.

    Leitplanke → ``UngueltigeEinstellungError``; sonst unbekannter Key →
    ``UnknownSettingError``; Wert ausserhalb der Registry → ``UngueltigeEinstellungError``.
    Beides 422 im Endpoint. Es wird nie geklemmt.
    """
    pruefe_name(name)
    leitplanken = sorted(k for k in werte if k in LEITPLANKEN_KEYS)
    if leitplanken:
        raise ts.UngueltigeEinstellungError(
            "Compliance guardrails are never part of a strategy: "
            + ", ".join(leitplanken),
            code="guardrail_in_strategy",
            params={"keys": leitplanken},
        )
    unbekannt = [k for k in werte if k not in ts.REGISTRY]
    if unbekannt:
        raise ts.UnknownSettingError(unbekannt)
    kanonisch = {k: _pruefe_wert(ts.REGISTRY[k], werte[k]) for k in sorted(werte)}
    # Quelle ohne Prompt: der Satz muss fuer sich stehen, Basis sind die Registry-Defaults.
    ts.pruefe_llm_ranking(kanonisch, {})
    return kanonisch


def liste_verworfen(werte: Dict[str, str]) -> Optional[Dict[str, str]]:
    """#4660: Pruefgrund je verworfenem Symbol der eigenen Liste, sonst None.

    Dieselbe Pruefung wie ``ts.pruefe_llm_ranking`` in ``pruefe_strategie``: statische
    S&P-500-Liste, deterministisch, ohne Netz. Fehlt ``ROUND_TABLE_TOP_K_EVAL`` im Satz,
    gilt der Registry-Default — wie dort (Basis ``{}``).
    """
    if werte.get("LLM_RANKING_QUELLE") != "liste":
        return None
    from core.engine.llm_ranking import pruefe_symbolliste
    from core.round_table.sp500_universe import get_sp500_symbols

    top_k = werte.get(
        "ROUND_TABLE_TOP_K_EVAL", ts.REGISTRY["ROUND_TABLE_TOP_K_EVAL"].default
    )
    _, verworfen = pruefe_symbolliste(
        werte.get("LLM_RANKING_LISTE", ""), set(get_sp500_symbols()), int(top_k)
    )
    return verworfen


def pruefe_obergrenze(bestand: Dict[str, Dict[str, str]], name: str) -> None:
    """Eine NEUE Strategie ueber ``MAX_STRATEGIEN`` hinaus → ``UngueltigeEinstellungError``."""
    if name not in bestand and len(bestand) >= MAX_STRATEGIEN:
        raise ts.UngueltigeEinstellungError(
            f"At most {MAX_STRATEGIEN} strategies — delete one first",
            code="strategy_limit_reached",
            params={"limit": MAX_STRATEGIEN},
        )


def werte_fuer_kette(werte: Dict[str, str]) -> str:
    """Kanonischer, sortierter JSON-Text fuer das WORM-Feld ``werte``.

    ``text``-Felder (der Ranking-Prompt) erreichen die Kette nur als ``sha256:<hex>`` —
    derselbe Hash wie ``ts.worm_changes`` (#4654); dieselbe Regel ``ts.ist_kettenhash``,
    also die eigene Liste im Klartext (#4861).
    """
    kette = {
        k: (ts._ketten_text(v) if ts.ist_kettenhash(k) else v) for k, v in werte.items()
    }
    return json.dumps(kette, sort_keys=True, separators=(",", ":"))


def strategie_hash(werte: Dict[str, str]) -> str:
    """#4630: ``sha256:<hex>`` ueber ``werte_fuer_kette`` — die Version des zugewiesenen Satzes."""
    text = werte_fuer_kette(werte)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Ablage ───────────────────────────────────────────────────────────────────


def ablage_pfad() -> Path:
    """``<AAA_USER_DATA_DIR>/strategien.json`` (desktop), sonst CWD-relativ (dev)."""
    return Path(resolve_audit_log_path(ABLAGE_DATEI))


def lade_strategien(pfad: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Gespeicherte Strategien; fehlt die Datei, ist das ein leerer Bestand."""
    pfad = pfad or ablage_pfad()
    if not pfad.exists():
        return {}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        strategien = daten["strategien"]
        if not isinstance(strategien, dict):
            raise ValueError("strategien ist kein Objekt")
        return {
            str(n): {str(k): str(v) for k, v in w.items()}
            for n, w in strategien.items()
        }
    except Exception as exc:  # noqa: BLE001 — jede Form von unlesbar
        logger.warning("[STRATEGIE] %s nicht lesbar: %s", pfad, exc)
        raise StrategieAblageError(f"{pfad.name} is not readable") from exc


def speichere_strategien(
    strategien: Dict[str, Dict[str, str]], pfad: Optional[Path] = None
) -> None:
    """Atomar: Temp-Datei, dann ``os.replace``. Ein Abbruch laesst die alte Datei stehen.

    #4863: Die Namen stehen in der uebergebenen Reihenfolge — das ist die Anlagereihenfolge
    (ADR-027 Regel 2). Deterministisch bleibt die Datei, weil die Werte je Strategie nach
    Key sortiert geschrieben werden.
    """
    pfad = pfad or ablage_pfad()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".json.tmp")
    geordnet = {n: dict(sorted(w.items())) for n, w in strategien.items()}
    text = json.dumps({"strategien": geordnet}, indent=2)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, pfad)
    finally:
        if tmp.exists():
            tmp.unlink()


# ── Versionen (#4863) ────────────────────────────────────────────────────────
# Modelle lokal wie oben (Plan §2 Punkt 4); hier am Ende, damit die Zeilenanker nicht wandern.


class StrategyVersion(BaseModel):
    """Eine Version aus der Kette. ``hash`` ist bytegleich mit ``strategie_hash``."""

    hash: str
    hash_kuerzel: str
    zeitpunkt: str
    aktion: str
    felder: List[str]


class StrategyVersionsResponse(BaseModel):
    """Body von ``GET /api/strategies/{name}/versions`` — neueste Version zuerst."""

    name: str
    versionen: List[StrategyVersion]


def lies_versionen(log_dir: Path, name: str) -> List[StrategyVersion]:
    """Versionen von ``name`` aus den ``strategy_definition``-Zeilen der Kette, neueste zuerst.

    Rein lesend und blockierend — die Route ruft es in einem Thread. Reihenfolge ist die der
    Kette (Tagesdatei nach Name, darin Zeile), nicht ``timestamp``. Gezaehlt wird nur der
    Zyklus seit dem letzten ``create``; ein ``delete`` beendet ihn und ist selbst keine
    Version. ``felder`` nennt bei ``create`` alle Keys, bei ``update`` die gegenueber der
    Vorversion hinzugekommenen, entfallenen oder geaenderten — Prompt-Werte stehen auf der
    Kette nur als Hash, der Klartext kommt nie heraus.

    Ein nicht lesbares Verzeichnis oder eine nicht lesbare Datei ist ``OSError`` (503 in der
    Route), nie eine still leere Liste. Unlesbare Zeilen werden uebersprungen und gezaehlt.
    """
    from core.round_table.senate_log import LocalJSONAuditLogger

    dateien = sorted(
        f
        for f in Path(log_dir).iterdir()
        if f.is_file() and LocalJSONAuditLogger._DATE_FILE_RE.match(f.name)
    )
    zyklus: List[Dict[str, Any]] = []
    hat_create = False
    unlesbar = 0
    for datei in dateien:
        with datei.open("r", encoding="utf-8") as f:
            for zeile in f:
                if '"strategy_definition"' not in zeile:
                    continue
                try:
                    eintrag = json.loads(zeile)
                    if eintrag.get("event_type") != "strategy_definition":
                        continue
                    if eintrag.get("name") != name:
                        continue
                    aktion = eintrag["aktion"]
                    werte_text = eintrag["werte"]
                    werte = json.loads(werte_text)
                    if not isinstance(werte, dict):
                        raise TypeError("werte ist kein Objekt")
                    zeitpunkt = eintrag["timestamp"]
                except (ValueError, KeyError, TypeError, AttributeError):
                    unlesbar += 1
                    continue
                if aktion == "create":
                    zyklus, hat_create = [], True
                elif aktion == "delete":
                    zyklus, hat_create = [], False
                    continue
                zyklus.append(
                    {
                        "aktion": aktion,
                        "werte_text": werte_text,
                        "werte": werte,
                        "zeitpunkt": zeitpunkt,
                    }
                )
    if unlesbar:
        logger.warning(
            "[STRATEGIE] %d unlesbare Kettenzeile(n) in %s uebersprungen",
            unlesbar,
            log_dir,
        )
    if not hat_create:
        logger.warning(
            "[STRATEGIE] %r: Ablage ohne Ketten-Eintrag (kein create in %s) — "
            "%d Version(en) gefunden",
            name,
            log_dir,
            len(zyklus),
        )

    versionen: List[StrategyVersion] = []
    vorher: Dict[str, Any] = {}
    for e in zyklus:
        werte = e["werte"]
        if e["aktion"] == "create":
            felder = sorted(werte)
        else:
            felder = sorted(
                k for k in set(werte) | set(vorher) if werte.get(k) != vorher.get(k)
            )
        digest = hashlib.sha256(e["werte_text"].encode("utf-8")).hexdigest()
        versionen.append(
            StrategyVersion(
                hash="sha256:" + digest,
                hash_kuerzel=digest[:8],
                zeitpunkt=e["zeitpunkt"],
                aktion=e["aktion"],
                felder=felder,
            )
        )
        vorher = werte
    return list(reversed(versionen))


# ── Zuweisungen (#4891) ──────────────────────────────────────────────────────
# ``zuweisungen.json`` spiegelt je Konto den letzten ``StrategyAssignmentEvent`` der Kette.
# Die Kette bleibt die Quelle der Wahrheit; der Spiegel macht Lesepfad und Loeschsperre
# unabhaengig von der Kettengroesse (Plan §2, Option A). Geschrieben wird er nur NACH dem
# WORM-Eintrag und unter ``SPERRE``. Am Ende der Datei, damit die Zeilenanker nicht wandern.

ZUWEISUNGEN_DATEI = "zuweisungen.json"


class StrategyAssignment(BaseModel):
    """Die zuletzt versiegelte Zuweisung eines Kontos — nicht die laufende Konfiguration."""

    konto: str
    strategie: str
    strategie_hash: str
    timestamp: str


class StrategyAssignmentsResponse(BaseModel):
    """Body von ``GET /api/strategy-assignments`` — leere Liste = keine Zuweisung."""

    zuweisungen: List[StrategyAssignment]


def zuweisungen_pfad() -> Path:
    """``<AAA_USER_DATA_DIR>/zuweisungen.json`` neben ``strategien.json``."""
    return Path(resolve_audit_log_path(ZUWEISUNGEN_DATEI))


def lade_zuweisungen(pfad: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    """Konto → Zuweisung; fehlt die Datei, gibt es keine. Unlesbar wie ``lade_strategien``."""
    pfad = pfad or zuweisungen_pfad()
    if not pfad.exists():
        return {}
    try:
        daten = json.loads(pfad.read_text(encoding="utf-8"))
        zuweisungen = daten["zuweisungen"]
        if not isinstance(zuweisungen, dict):
            raise ValueError("zuweisungen ist kein Objekt")
        ergebnis = {}
        for konto, eintrag in zuweisungen.items():
            if not isinstance(eintrag, dict):
                raise ValueError(f"Eintrag fuer Konto {konto!r} ist kein Objekt")
            for feld in ("strategie", "strategie_hash", "timestamp"):
                wert = eintrag.get(feld)
                if not isinstance(wert, str) or not wert.strip():
                    raise ValueError(
                        f"Feld {feld!r} fehlt oder ist leer fuer Konto {konto!r}"
                    )
            ergebnis[str(konto)] = {str(k): str(v) for k, v in eintrag.items()}
        return ergebnis
    except Exception as exc:  # noqa: BLE001 — jede Form von unlesbar
        logger.warning("[STRATEGIE] %s nicht lesbar: %s", pfad, exc)
        raise StrategieAblageError(f"{pfad.name} is not readable") from exc


def speichere_zuweisung(
    konto: str,
    strategie: str,
    strategie_hash: str,
    timestamp: str,
    pfad: Optional[Path] = None,
) -> None:
    """Setzt den Eintrag des Kontos; atomar wie ``speichere_strategien``."""
    pfad = pfad or zuweisungen_pfad()
    zuweisungen = lade_zuweisungen(pfad)
    zuweisungen[konto] = {
        "strategie": strategie,
        "strategie_hash": strategie_hash,
        "timestamp": timestamp,
    }
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_suffix(".json.tmp")
    text = json.dumps({"zuweisungen": dict(sorted(zuweisungen.items()))}, indent=2)
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, pfad)
    finally:
        if tmp.exists():
            tmp.unlink()


def zugewiesene_konten(zuweisungen: Dict[str, Dict[str, str]], name: str) -> List[str]:
    """Die Konten, denen ``name`` zugewiesen ist, nach Konto-Schluessel."""
    return sorted(k for k, z in zuweisungen.items() if z.get("strategie") == name)
