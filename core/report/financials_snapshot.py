"""#3145 (bundle) — ship a pre-derived fundamentals cache so the agents work
**offline by default**.

The nightly SEC producer warms ``data/financials_cache/<SYMBOL>.json``. To make the
desktop functional on first run (no network, no key), a single gzipped snapshot of
that cache is bundled and expanded once on a cold cache. The snapshot is a plain
mapping ``{SYMBOL: cache_payload}`` — the exact per-symbol payload the reader already
consumes — so seeding is a pure file expand, and a later nightly run supersedes it.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

# #4642: Dieses Verzeichnis hat keinen Namensraum. ``reentry_lockout.py`` legt seinen
# Zustand bewusst dorthin, weil ``DEFAULT_CACHE_DIR`` der einzige schreibbare Pfad ist,
# der auf dem Desktop ein Upgrade uebersteht (``AAA_USER_DATA_DIR``) — und jeder
# Verbraucher hier behandelte bisher JEDE ``*.json`` als Wertpapier, denn der Dateiname
# *ist* das Symbol. Gemessen am 21./22.10.2026 an der Live-Ablage waren drei Wirkungen
# da: ein Titel ``REENTRY_LOCKOUT`` im Abbild (Inhalts-Tor aus #4612 dauerhaft rot), ein
# verdecktes Ablage-Alter (siehe ``melde_ablage_alter``) und eine Erstsaat, die einen
# Fremdschluessel als Titeldatei anlegt.
#
# Das Muster deckt bis sechs Zeichen, Punkt und Bindestrich (``BRK.B``, ``BF.B``).
# Gegengeprueft am 22.10.2026 gegen alle 540 Namen der Live-Ablage: 539 Treffer,
# 1 Nicht-Treffer (``reentry_lockout.json``), kein Fehltreffer.
SYMBOL_MUSTER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,5}$")


def ist_symbol(name: str) -> bool:
    """Ist ``name`` (ohne ``.json``) ein Wertpapier-Symbol und keine Nachbardatei?

    Eine Stelle fuer alle drei Verbraucher, damit nicht jeder seine eigene Vorstellung
    von „Symbol" bekommt.
    """
    return bool(SYMBOL_MUSTER.match(str(name).strip()))


# The read-only snapshot ships in the app bundle (CWD-relative — always present in
# every install, re-extracted on upgrade). The writable cache it seeds lives under
# AAA_USER_DATA_DIR (persistent) — see financials_feed._resolve_cache_dir.
DEFAULT_SNAPSHOT_PATH = os.path.join("data", "financials_snapshot.json.gz")


def _open_text(path: str, mode: str):
    return (
        gzip.open(path, mode, encoding="utf-8")
        if str(path).endswith(".gz")
        else open(path, mode, encoding="utf-8")
    )


def build_snapshot(cache_dir: str, out_path: str) -> int:
    """Pack every ``<SYMBOL>.json`` in ``cache_dir`` into one snapshot at ``out_path``.

    ``out_path`` ending in ``.gz`` is gzip-compressed. Returns the symbol count.
    """
    snap: Dict[str, Any] = {}
    if os.path.isdir(cache_dir):
        for name in sorted(os.listdir(cache_dir)):
            if not name.endswith(".json"):
                continue
            symbol = name[:-5].upper()
            if not ist_symbol(symbol):
                # #4642: Eine Nachbardatei im Verzeichnis ist erwartet, keine
                # Auffaelligkeit — deshalb debug und nicht warning.
                logger.debug("snapshot: %s is not a symbol file — skipping.", name)
                continue
            try:
                with open(os.path.join(cache_dir, name), encoding="utf-8") as handle:
                    snap[symbol] = json.load(handle)
            except (OSError, ValueError) as exc:
                logger.warning("snapshot: skipping unreadable %s (%s).", name, exc)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with _open_text(out_path, "wt") as handle:
        json.dump(snap, handle)
    return len(snap)


def load_snapshot(path: str) -> Optional[Mapping[str, Any]]:
    if not path or not os.path.exists(path):
        return None
    try:
        with _open_text(path, "rt") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("snapshot: unreadable snapshot %s (%s).", path, exc)
        return None


def seed_cache_from_snapshot(snapshot_path: str, cache_dir: str) -> int:
    """Expand a bundled snapshot into the per-symbol cache — never overwriting.

    Returns the number of symbols seeded (0 when every symbol of the snapshot is
    already present, or no snapshot could be read).

    #4571: Der Vertrag "niemals ueberschreiben" gilt jetzt **je Symbol** statt je
    Verzeichnis. Vorher brach die Saat ab, sobald EINE einzige ``.json`` im Zielordner
    lag — mit 478 Dateien war sie damit ein dauerhafter Leerlauf. Der Aufruf beim
    Engine-Start lief, tat aber seit dem 03.09.2026 nichts; gemessen 476 Dateien, alle
    vom 03.09.2026 14:20. **Kein Update, kein Release und keine Neuinstallation ueber
    eine bestehende hinweg konnte den Inhalt je veraendern.**

    Die Absicht der Verzeichnis-Sperre (*"a nightly refresh is always fresher than the
    bundled snapshot"*) war richtig — nur gab es den naechtlichen Lauf nie. Damit war
    sie nicht der Schutz einer frischeren Quelle, sondern das Einfrieren der einzigen.

    Geschrieben wird ausschliesslich, was **nicht existiert**. Eine vorhandene Datei
    wird nicht geoeffnet, nicht verglichen und nicht angefasst — sie bleibt
    byte-gleich, und ihre Aenderungszeit bleibt stehen.
    """
    snap = load_snapshot(snapshot_path)
    if not snap:
        return 0
    os.makedirs(cache_dir, exist_ok=True)
    seeded = 0
    uebersprungen = 0
    fremd = 0
    for symbol, payload in snap.items():
        if not ist_symbol(str(symbol).strip().upper()):
            # #4642: Ein Abbild, das je einmal aus einer Live-Ablage gebaut wurde,
            # traegt Fremdschluessel. Die Saat darf daraus keine Titeldatei in der
            # Ablage des Nutzers anlegen.
            fremd += 1
            continue
        path = os.path.join(cache_dir, f"{str(symbol).strip().upper()}.json")
        if os.path.exists(path):
            uebersprungen += 1
            continue
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            seeded += 1
        except OSError as exc:
            logger.warning("snapshot: could not seed %s (%s).", symbol, exc)
    if seeded:
        logger.info(
            "snapshot: seeded %d missing symbols from %s (%d already present, "
            "%d non-symbol keys ignored).",
            seeded,
            snapshot_path,
            uebersprungen,
            fremd,
        )
    if fremd:
        # Ein Fremdschluessel im AUSGELIEFERTEN Abbild ist eine Auffaelligkeit — er
        # bedeutet, dass das Abbild aus einer Live-Ablage gebaut wurde (#4642).
        logger.warning(
            "snapshot: %d non-symbol keys in %s were ignored — the snapshot was "
            "likely built from a live cache directory (#4642).",
            fremd,
            snapshot_path,
        )
    return seeded


#: Ab diesem Alter ist die Ablage eine WARNING wert. Derselbe Wert wie
#: ``skip_fresh_days`` im Produzenten: was dieser auffrischen wuerde, gilt als alt.
ABLAGE_WARNFRIST_TAGE = 25

#: Mindestzahl Symbole fuer ein tragfaehiges Abbild. Die Live-Indexliste hatte am
#: 21.10.2026 503 Eintraege; 495 laesst Raum fuer einzelne Titel ohne CIK-Zuordnung,
#: ohne den Verfall auf 476 (Stand des alten Abbilds) zu erlauben.
ABBILD_MIN_SYMBOLE = 495

#: Obergrenze fuer den Median der Perioden je Titel. Jahresdaten aus der SEC reichen
#: etwa bis 2009, das sind ~20 Perioden; gemessen hat das neue Abbild **19**, das alte
#: **51**. 25 trennt beide klar.
#:
#: Bewusst ein AGGREGAT und keine Abstandspruefung je Titel: ein erster Versuch mit
#: "aufeinanderfolgende Jahresenden liegen >=300 Tage auseinander" erzeugte **84
#: Fehlalarme** auf tragfaehigen Daten — ``A 2018-10-31 -> 2018-11-01`` (ein Tag, zwei
#: Meldungen desselben Geschaeftsjahres ueber eine 52/53-Wochen-Grenze) und
#: ``ARES 2014-04-30 -> 2014-12-31`` (eine echte Verlegung des Geschaeftsjahresendes).
#: Ein Tor, das 84-mal falsch schreit, wird abgeschaltet statt beachtet.
ABBILD_MAX_PERIODEN_MEDIAN = 25

#: Titel, deren juengste Periode (noch) keinen Umsatz traegt — mit gemessener Ursache.
#: **Diese Liste ist eine Ratsche: sie darf nur kuerzer werden.** Gemessen am
#: 21.10.2026 gegen `data.sec.gov`:
#:   * FITB, RF, SYF, TFC — Banken ohne abgebildetes Umsatz-Kennzeichen. TFC meldet
#:     ``InterestAndDividendIncomeOperating``, das in SEC_TAG_MAP fehlt; dieselbe
#:     Klasse wie XEL in #4567. Gehoert in ein eigenes Ticket, nicht hierher.
#:   * APA, LHX, SKYD, VMRK — Kennzeichen IST abgebildet, erreicht aber die juengste
#:     Periode nicht. Andere Ursache, ebenfalls noch nicht zugeordnet.
ABBILD_BEKANNTE_LUECKEN = frozenset(
    {"APA", "FITB", "LHX", "RF", "SKYD", "SYF", "TFC", "VMRK"}
)


def pruefe_abbild(
    snap: Optional[Mapping[str, Any]],
    *,
    min_symbole: int = ABBILD_MIN_SYMBOLE,
    bekannte_luecken: "frozenset[str]" = ABBILD_BEKANNTE_LUECKEN,
) -> list:
    """Inhaltliche Pruefung eines Abbilds. Leere Liste = tragfaehig.

    #4612: Der bisherige Waechter prueft ``len(snap) > 400`` — **Anzahl statt Inhalt**.
    Gemessen am 21.10.2026 hat er damit ein Abbild durchgelassen, das von einem
    Produzenten **vor** dem Jahresdauer-Filter und **vor** der Sortierung aus #4567
    stammte:

    ==========================================  =========  ========
    gemessen am ausgelieferten Abbild             alt        neu
    ==========================================  =========  ========
    Symbole                                          476       500
    Perioden insgesamt                            20.837     9.091
    Median Perioden je Titel                          51        19
    neueste-Periode-zuerst sortiert              0 von 476  500/500
    juengste Periode ohne Umsatz (Agentenpfad)        47         8
    juengste Periode ohne Gewinn                      35         1
    ==========================================  =========  ========

    Die Folge war am Verbraucherpfad messbar: fuer **URI** waehlte
    ``derive_fundamentals`` die Periode ``2025-03-31`` — ein **Quartal** — als
    aktuelle Kennzahl. Laege dort ein Umsatz, bekaeme der Konsens ein Quartal als
    Jahreszahl. Und 47 Titel lieferten keine Marge, worauf der FundamentalsAgent sich
    mit ``no verified fundamentals data yet`` enthielt: das sieht wie eine Datenluecke
    aus und war ein veraltetes Abbild.

    Diese Funktion ist bewusst **eine** Stelle fuer beide Nutzer — den Test ueber das
    ausgelieferte Abbild und das Tor im Neubau-Workflow, das einen PR verweigert, wenn
    das frische Abbild schlechter ist als das, was es ersetzen soll.
    """
    befunde: list = []
    if not snap:
        return ["Abbild ist leer oder unlesbar"]
    if len(snap) < min_symbole:
        befunde.append(
            f"nur {len(snap)} Symbole, mindestens {min_symbole} erwartet "
            "(die Live-Indexliste hat ueber 500)"
        )

    ohne_umsatz: list = []
    ohne_gewinn: list = []
    unsortiert: list = []
    anzahlen: list = []
    for symbol, nutzlast in snap.items():
        sym = str(symbol).upper()
        zeilen = (nutzlast or {}).get("results") or []
        enden = [str(z.get("end_date")) for z in zeilen if z.get("end_date")]
        if not enden:
            ohne_umsatz.append(sym)
            continue
        anzahlen.append(len(enden))
        if enden != sorted(enden, reverse=True):
            unsortiert.append(sym)
        juengste = max(enden)
        zeile = next(z for z in zeilen if str(z.get("end_date")) == juengste)
        konto = (zeile.get("financials") or {}).get("income_statement") or {}
        if (konto.get("revenues") or {}).get("value") is None:
            ohne_umsatz.append(sym)
        if (konto.get("net_income_loss") or {}).get("value") is None:
            ohne_gewinn.append(sym)

    neu_ohne_umsatz = sorted(set(ohne_umsatz) - set(bekannte_luecken))
    neu_ohne_gewinn = sorted(set(ohne_gewinn) - set(bekannte_luecken))
    if neu_ohne_umsatz:
        befunde.append(
            f"{len(neu_ohne_umsatz)} Titel ohne Umsatz in der juengsten Periode, "
            f"ausserhalb der bekannten Luecken: {neu_ohne_umsatz[:12]}"
        )
    if neu_ohne_gewinn:
        befunde.append(
            f"{len(neu_ohne_gewinn)} Titel ohne Gewinn in der juengsten Periode, "
            f"ausserhalb der bekannten Luecken: {neu_ohne_gewinn[:12]}"
        )
    if unsortiert:
        befunde.append(
            f"{len(unsortiert)} Titel nicht neueste-Periode-zuerst sortiert: "
            f"{sorted(unsortiert)[:12]} — `_pit_select` waehlt dann bei gleichem "
            "Einreichungsdatum die falsche Periode"
        )
    if anzahlen:
        anzahlen.sort()
        median = anzahlen[len(anzahlen) // 2]
        if median > ABBILD_MAX_PERIODEN_MEDIAN:
            befunde.append(
                f"Median {median} Perioden je Titel, hoechstens "
                f"{ABBILD_MAX_PERIODEN_MEDIAN} erwartet — das Abbild traegt "
                "Quartalsdauern statt nur Jahresperioden (der Jahresdauer-Filter "
                "aus #4567 fehlte dem Erzeuger)"
            )
    return befunde


def melde_ablage_alter(
    cache_dir: Optional[str] = None,
    *,
    frist_tage: int = ABLAGE_WARNFRIST_TAGE,
) -> Optional[int]:
    """Alter der Fundamentals-Ablage ins Protokoll. Rueckgabe: Alter in Tagen.

    #4571: Heute altert die Ablage **lautlos**. Gemessen am 20.10.2026: 476 Dateien,
    alle vom 03.09.2026 14:20 — **47 Tage alt**, und kein Wort davon im Protokoll. Der
    ``FundamentalsAgent`` meldete stattdessen ``EXCLUDED — no verified fundamentals
    data yet``, was wie eine Datenluecke aussieht und keine war. Diese Blindheit ist
    der Grund, warum das Ticket ueberhaupt entstanden ist.

    **Beim Start und nicht je Zyklus:** dort laeuft die Saat schon, eine Altersmeldung
    gehoert in dieselbe Boot-Diagnose — und je Zyklus stuende dieselbe WARNING alle
    paar Minuten im Protokoll, was eine Warnung entwertet.

    Gibt ``None`` zurueck, wenn es nichts zu messen gibt (leere oder fehlende Ablage —
    beim ersten Start der Normalfall, kein Altersproblem). Darf den Start **nie**
    aufhalten.
    """
    if cache_dir:
        ziel = cache_dir
    else:
        # Lokaler Import wie in ``seed_bundled_fundamentals``: ``financials_feed`` haelt
        # den Pfad, und ein Modul-Import hier waere ein Ringschluss.
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        ziel = DEFAULT_CACHE_DIR
    try:
        # #4642: Nur Titeldateien. Die Rueckkauf-Sperrliste liegt im selben
        # Verzeichnis und wird bei jeder Ausstoppung neu geschrieben — als juengste
        # Datei verdeckte sie das Alter der Fundamentaldaten. Gemessen am 20.10.2026:
        # gerechnet wurden 20 Tage (Sperrliste vom 29.09.), wahr waren 33 (juengste
        # Titeldatei vom 16.09.) — unter der Frist von 25, also blieb die Warnung aus,
        # die #4571 genau fuer diesen Fall gebaut hat.
        namen = [
            n
            for n in os.listdir(ziel)
            if n.endswith(".json") and ist_symbol(n[:-5].upper())
        ]
    except OSError:
        return None
    if not namen:
        return None

    from core.sim.clock import engine_now

    juengste = max(os.stat(os.path.join(ziel, n)).st_mtime for n in namen)
    stand = datetime.fromtimestamp(juengste, tz=timezone.utc)
    alter = (engine_now(timezone.utc) - stand).days

    if alter >= frist_tage:
        logger.warning(
            "Fundamentals-Ablage ist %d Tage alt: %d Dateien, juengste vom %s (%s). "
            "Neu aufgenommene Titel bekommen keine Kennzahlen, und der "
            "FundamentalsAgent enthaelt sich dort mit 'no verified fundamentals data "
            "yet' — das sieht wie eine Datenluecke aus, ist aber Alter (#4571).",
            alter,
            len(namen),
            stand.date().isoformat(),
            ziel,
        )
    else:
        logger.info(
            "Fundamentals-Ablage: %d Dateien, juengste vom %s (%d Tage).",
            len(namen),
            stand.date().isoformat(),
            alter,
        )
    return alter


def seed_bundled_fundamentals(
    cache_dir: Optional[str] = None, snapshot_path: Optional[str] = None
) -> int:
    """Boot convenience: seed the persistent cache from the bundled snapshot.

    Resolves the writable cache dir (AAA_USER_DATA_DIR on desktop) and the bundled
    snapshot path by default. Cold-cache-only + best-effort — a missing snapshot or
    an already-warm cache returns 0 and never raises.
    """
    if cache_dir is None:
        from core.report.financials_feed import DEFAULT_CACHE_DIR

        cache_dir = DEFAULT_CACHE_DIR
    return seed_cache_from_snapshot(snapshot_path or DEFAULT_SNAPSHOT_PATH, cache_dir)
