"""#3468 — abgelegte Entscheidungssätze kehren in die Datenbank zurück.

Scheitert ein ORM-Insert des ``CloudLogger``, schreibt er die Charge nach
``cloud_fallback_logs/<tabelle>_<JJJJMMTT>.jsonl`` (``CloudLogger._write_fallback``). Zurück
kam von dort bisher nichts. Nach der Schemadrift aus #3373 lagen auf einer laufenden
Installation 3 926 Entscheidungssätze nur in dieser Ablage — die Tabellen ``decisions`` und
``decision_outcomes`` hatten eine Woche Loch.

Dieses Modul liest die Ablage nach, **nachdem** das Schema geheilt ist — beim Start der
lokalen Datenbank (``core/database/session.py``) und einmalig per Kommando::

    python -m core.database.fallback_nachlesen --db <sqlite-datei> --ablage <verzeichnis> [--probelauf]

**Grundsätze.**

* **Nur einfügen, nie ändern.** ``INSERT OR IGNORE`` über den Primärschlüssel; ein Satz, der
  schon steht, bleibt unangetastet. Mehrfaches Nachlesen ist darum harmlos.
* **Nie löschen.** Eine vollständig nachgelesene Datei wird nach ``nachgelesen/`` verschoben.
  Die Datei von **heute** bleibt liegen — der Logger kann noch an sie anhängen.
* **Fremdschlüssel beachten.** Die lokale DB läuft mit ``PRAGMA foreign_keys=ON``, und
  ``INSERT OR IGNORE`` fängt einen Fremdschlüssel-Verstoß **nicht** ab. Ein Ergebnis ohne
  zugehörige Entscheidung wird darum vorher aussortiert und gezählt, statt die ganze Datei
  scheitern zu lassen. Entscheidungen werden vor Ergebnissen gelesen.
* **Nie den Start anhalten.** Was scheitert, bleibt liegen und wird auf WARNING gemeldet.

**Warum eigene Helfer statt der aus ``core/cloud_logger.py``:** Der Import dort instanziiert
den Logger samt Worker-Thread und eigener DB-Engine (``logger_instance = CloudLogger()``). Aus
dem DB-Start heraus wäre das ein Seiteneffekt. Dieses Modul läuft nur auf SQLite und braucht
davon nichts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from sqlalchemy import insert, select

logger = logging.getLogger(__name__)

ABGELEGT = "nachgelesen"

# Reihenfolge ist Pflicht: decision_outcomes.decision_id verweist auf decisions.
_TABELLEN = ("decisions", "decision_outcomes")

# SQLite erlaubt je Anweisung nur eine begrenzte Zahl Platzhalter.
_BLOCK = 500

# Zeilen je Verarbeitungsschritt. Die Ablage kann gross werden (im Repo lagen 1,3 GB
# Hinterlassenschaften von Testlaeufen, einzelne Dateien ueber 80 MB) — eine Datei wird darum
# zeilenweise gelesen, nie auf einmal.
_STAPEL = 1000


@dataclass
class Bericht:
    dateien: int = 0
    eingefuegt: int = 0
    schon_vorhanden: int = 0
    ohne_entscheidung: int = 0
    unlesbar: int = 0
    gescheitert: int = 0


def automatisch_zulaessig() -> bool:
    """Nachlesen beim Start nur auf einer echten Desktop-Installation.

    Ohne ``AAA_USER_DATA_DIR`` (Entwicklung, Cloud, Tests) ist die Ablage ein
    Arbeitsverzeichnis-Relikt — im Repo lagen dort 1,3 GB Hinterlassenschaften von
    Testlaeufen. Das beim Start einzulesen war nie gemeint. Das Kommando bleibt fuer
    Hand-Einsaetze.
    """
    return bool(os.environ.get("AAA_USER_DATA_DIR", "").strip())


def ablage_verzeichnis() -> Path:
    """Dieselbe Regel wie ``core.cloud_logger._resolve_fallback_dir`` (Test hält beide gleich)."""
    user_data_dir = os.environ.get("AAA_USER_DATA_DIR", "").strip()
    if user_data_dir:
        return Path(user_data_dir) / "cloud_fallback_logs"
    return Path("cloud_fallback_logs")


def _modelle():
    from core.database.models import Decision, DecisionOutcome

    return {"decisions": Decision, "decision_outcomes": DecisionOutcome}


def _als_zeit(wert) -> Optional[datetime]:
    """ISO-Text → zeitzonenbewusst. ``None`` statt „jetzt", wenn es nicht passt.

    Der Logger fällt an dieser Stelle auf ``datetime.now()`` zurück. Beim Nachlesen wäre das
    falsch: Ein Satz vom 11.09. bekäme den Zeitstempel des Nachlesens.
    """
    if isinstance(wert, datetime):
        return wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
    if not isinstance(wert, str):
        return None
    try:
        text = wert[:-1] + "+00:00" if wert.endswith("Z") else wert
        zeit = datetime.fromisoformat(text)
    except ValueError:
        return None
    return zeit if zeit.tzinfo else zeit.replace(tzinfo=timezone.utc)


def _dateien(verzeichnis: Path, tabelle: str) -> List[tuple]:
    muster = re.compile(rf"^{re.escape(tabelle)}_(\d{{8}})\.jsonl$")
    gefunden = []
    for pfad in sorted(verzeichnis.glob(f"{tabelle}_*.jsonl")):
        treffer = muster.match(pfad.name)
        if treffer:
            gefunden.append(
                (pfad, datetime.strptime(treffer.group(1), "%Y%m%d").date())
            )
    return gefunden


def _stapel(pfad: Path, modell, bericht: Bericht) -> Iterator[List[dict]]:
    """Liefert die Saetze einer Datei in Stapeln — zeilenweise gelesen, nie auf einmal."""
    spalten = set(modell.__table__.columns.keys())
    pk = modell.__table__.primary_key.columns.keys()[0]
    saetze: Dict[str, dict] = {}
    with pfad.open(encoding="utf-8") as datei:
        for zeile in datei:
            if not zeile.strip():
                continue
            try:
                roh = json.loads(zeile)
            except ValueError:
                bericht.unlesbar += 1
                continue
            if not isinstance(roh, dict) or not roh.get(pk):
                bericht.unlesbar += 1
                continue
            # Felder, die die Tabelle nicht kennt, fallen weg — derselbe Grundsatz wie
            # im Logger (MiFID-25): Das Modell ist die Autoritaet ueber das Schema.
            satz = {k: v for k, v in roh.items() if k in spalten}
            if "decision_time" in satz:
                zeit = _als_zeit(satz["decision_time"])
                if zeit is None:
                    bericht.unlesbar += 1
                    continue
                satz["decision_time"] = zeit
            saetze[satz[pk]] = satz  # im Stapel: der letzte Stand gewinnt
            if len(saetze) >= _STAPEL:
                yield list(saetze.values())
                saetze = {}
    if saetze:
        yield list(saetze.values())


async def _vorhanden(conn, spalte, schluessel: List[str]) -> set:
    gefunden: set = set()
    for i in range(0, len(schluessel), _BLOCK):
        block = schluessel[i : i + _BLOCK]
        ergebnis = await conn.execute(select(spalte).where(spalte.in_(block)))
        gefunden.update(r[0] for r in ergebnis)
    return gefunden


async def _lies_datei_nach(
    engine, pfad: Path, tabelle: str, bericht, probelauf, vorgemerkt: set
):
    """Eine Datei, eine Transaktion — verarbeitet in Stapeln.

    Die Transaktion umfasst die ganze Datei, damit ein Probelauf wirklich nichts
    hinterlaesst und eine gescheiterte Datei ganz liegen bleibt. Spaetere Stapel sehen die
    Saetze frueherer Stapel derselben Transaktion — ein Satz, der zweimal in der Datei steht,
    wird darum nur einmal gezaehlt.

    ``vorgemerkt``: Entscheidungen, die ein **Probelauf** einfuegen wuerde, aber zurueckgerollt
    hat. Ohne sie zaehlte der Probelauf jedes zugehoerige Ergebnis als „ohne Entscheidung" —
    an einer Kopie der Live-DB 3 926 Stueck, der echte Lauf 0.
    """
    modelle = _modelle()
    modell = modelle[tabelle]
    pk_spalte = list(modell.__table__.primary_key.columns)[0]
    ohne_gesamt = 0

    async with engine.connect() as conn:
        trans = await conn.begin()
        try:
            for saetze in _stapel(pfad, modell, bericht):
                schluessel = [s[pk_spalte.name] for s in saetze]
                schon = await _vorhanden(conn, pk_spalte, schluessel)
                neu = [s for s in saetze if s[pk_spalte.name] not in schon]
                bericht.schon_vorhanden += len(saetze) - len(neu)

                if tabelle == "decision_outcomes" and neu:
                    bekannt = await _vorhanden(
                        conn,
                        modelle["decisions"].__table__.c.decision_id,
                        [s["decision_id"] for s in neu],
                    )
                    ohne = len(neu)
                    # Nur vorgemerkt heisst: im Probelauf zurueckgerollt. Solche Saetze
                    # werden gezaehlt, aber nicht geschrieben — sie verletzten sonst
                    # den Fremdschluessel, den der echte Lauf erfuellt.
                    nur_gezaehlt = [
                        s
                        for s in neu
                        if s["decision_id"] not in bekannt
                        and s["decision_id"] in vorgemerkt
                    ]
                    neu = [s for s in neu if s["decision_id"] in bekannt]
                    ohne_gesamt += ohne - len(neu) - len(nur_gezaehlt)
                    bericht.eingefuegt += len(nur_gezaehlt)

                for satz in neu:
                    await conn.execute(
                        insert(modell).values(**satz).prefix_with("OR IGNORE")
                    )
                bericht.eingefuegt += len(neu)
                if probelauf and tabelle == "decisions":
                    vorgemerkt.update(s["decision_id"] for s in neu)
        except Exception:
            await trans.rollback()
            raise
        if probelauf:
            await trans.rollback()
        else:
            await trans.commit()

    if ohne_gesamt:
        bericht.ohne_entscheidung += ohne_gesamt
        logger.warning(
            "Fallback-Nachlesen: %d Ergebnis(se) in %s ohne zugehoerige Entscheidung "
            "— nicht eingefuegt (Fremdschluessel, #3468).",
            ohne_gesamt,
            pfad.name,
        )


def _lege_beiseite(pfad: Path) -> None:
    ziel_verzeichnis = pfad.parent / ABGELEGT
    ziel_verzeichnis.mkdir(exist_ok=True)
    ziel = ziel_verzeichnis / pfad.name
    n = 1
    while ziel.exists():
        ziel = ziel_verzeichnis / f"{pfad.stem}.{n}{pfad.suffix}"
        n += 1
    shutil.move(str(pfad), str(ziel))


async def lies_nach(
    engine,
    verzeichnis,
    *,
    heute: Optional[date] = None,
    probelauf: bool = False,
) -> Dict[str, Bericht]:
    """Liest die Fallback-Ablage für ``decisions`` und ``decision_outcomes`` nach.

    Wirft nicht. Gibt je Tabelle einen ``Bericht`` zurück.
    """
    verzeichnis = Path(verzeichnis)
    heute = heute or datetime.now().date()
    berichte = {t: Bericht() for t in _TABELLEN}
    vorgemerkt: set = set()
    if not verzeichnis.is_dir():
        return berichte

    for tabelle in _TABELLEN:
        bericht = berichte[tabelle]
        if tabelle == "decision_outcomes" and berichte["decisions"].gescheitert:
            # Scheiterten Entscheidungen, fehlte jedem zugehoerigen Ergebnis die
            # Entscheidung — es wuerde aussortiert und die Datei beiseitegelegt. Lieber
            # liegen lassen, bis die Entscheidungen durch sind.
            logger.warning(
                "Fallback-Nachlesen: decision_outcomes uebersprungen, weil "
                "Entscheidungen gescheitert sind — die Dateien bleiben liegen (#3468)."
            )
            continue
        for pfad, tag in _dateien(verzeichnis, tabelle):
            bericht.dateien += 1
            try:
                await _lies_datei_nach(
                    engine, pfad, tabelle, bericht, probelauf, vorgemerkt
                )
            except Exception as exc:
                bericht.gescheitert += 1
                logger.warning(
                    "Fallback-Nachlesen: %s nicht nachgelesen, bleibt liegen: %s (#3468)",
                    pfad.name,
                    str(exc).splitlines()[0][:200] if str(exc) else type(exc).__name__,
                )
                continue
            if not probelauf and tag != heute:
                _lege_beiseite(pfad)

    for tabelle, b in berichte.items():
        if b.dateien:
            # WARNING, nicht INFO: Dass es ueberhaupt etwas nachzulesen gab, heisst, dass
            # Schreibvorgaenge gescheitert waren — das soll der Betreiber sehen.
            logger.warning(
                "Fallback-Nachlesen %s%s: %d Datei(en), %d eingefuegt, %d schon vorhanden, "
                "%d ohne Entscheidung, %d unlesbar, %d gescheitert (#3468).",
                tabelle,
                " (Probelauf)" if probelauf else "",
                b.dateien,
                b.eingefuegt,
                b.schon_vorhanden,
                b.ohne_entscheidung,
                b.unlesbar,
                b.gescheitert,
            )
    return berichte


def _haupt(argv=None) -> int:
    import argparse

    from sqlalchemy.ext.asyncio import create_async_engine

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Pfad zur SQLite-Datei")
    parser.add_argument("--ablage", default=None, help="Fallback-Verzeichnis")
    parser.add_argument("--probelauf", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    async def _lauf():
        engine = create_async_engine(f"sqlite+aiosqlite:///{args.db}")
        from sqlalchemy import event

        @event.listens_for(engine.sync_engine, "connect")
        def _pragma(dbapi_connection, _):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        try:
            return await lies_nach(
                engine,
                args.ablage or ablage_verzeichnis(),
                probelauf=args.probelauf,
            )
        finally:
            await engine.dispose()

    berichte = asyncio.run(_lauf())
    for tabelle, b in berichte.items():
        print(f"{tabelle}: {b}")
    return 1 if any(b.gescheitert for b in berichte.values()) else 0


if __name__ == "__main__":
    raise SystemExit(_haupt())
