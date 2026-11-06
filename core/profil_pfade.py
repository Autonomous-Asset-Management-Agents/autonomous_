"""#4631 (STW-5) — Datenpfade je Profil: mehrere Engines nebeneinander, jede in ihrem Verzeichnis.

Gilt **nur** bei ``STRATEGY_PROFILES_ENABLED=true``. Bei aus liefert jeder Helfer den Altpfad
byte-gleich — dieselbe Datei, derselbe Text, dieselbe CWD-Relativitaet wie vor #4631.

Bei an liegen veraenderliche Dateien unter ``AAA_USER_DATA_DIR``, dem Datenverzeichnis des
Profils, das der Launcher (#4632) je Engine setzt. Eine Altdatei aus ``DATA_DIR`` (dem Bundle)
wird beim ersten Zugriff einmal **kopiert**, nie verschoben: Ein Rollback auf Flag aus findet
die Originaldatei unveraendert vor.

**Warum hier und ohne ``settings``-Import.** ``settings.py`` und ``core/keychain.py`` lesen
diese Helfer; ``config.oss.py`` laedt den Schluesselbund **vor** ``import settings``. Darum
liest das Modul das Flag direkt aus ``os.environ``, in genau der Lesart von
``settings.STRATEGY_PROFILES_ENABLED`` (``.lower() == "true"``) — eine zweite Lesart liesse
einen Pfad umschalten, waehrend die Konfiguration das Flag fuer aus haelt.

Plan: ``docs/4631-*/implementation_plan.md`` §2.1.
"""

from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

#: Verzeichnisname des Marktdaten-Caches (vor #4631 CWD-relativ, ``core/data_provider.py``).
MARKT_CACHE = "market_data_cache"


def aktiv() -> bool:
    """Ist ``STRATEGY_PROFILES_ENABLED`` an? Lesart wie in ``settings.py``."""
    return os.getenv("STRATEGY_PROFILES_ENABLED", "").lower() == "true"


def _datenverzeichnis() -> str:
    return os.getenv("AAA_USER_DATA_DIR", "").strip()


def nutzer_datei(alt: str) -> str:
    """Wo eine veraenderliche Datei liegt, die vor #4631 unter ``alt`` lag.

    Flag aus oder kein ``AAA_USER_DATA_DIR``: ``alt``, unveraendert. Sonst derselbe Dateiname
    im Datenverzeichnis des Profils; eine vorhandene Altdatei wird dorthin kopiert
    (``uebernimm_altdatei``).
    """
    verzeichnis = _datenverzeichnis()
    if not aktiv() or not verzeichnis:
        return alt
    neu = os.path.join(verzeichnis, os.path.basename(alt))
    if os.path.abspath(neu) != os.path.abspath(alt):
        uebernimm_altdatei(alt, neu)
    return neu


def markt_cache_verzeichnis() -> str:
    """Das Verzeichnis des Marktdaten-Caches. Flag aus: CWD-relativ wie vor #4631.

    Kein Uebertrag: Der Cache ist aus dem Netz wiederherstellbar, und ein Kopieren mehrerer
    Jahre Kurse je Profil waere teurer als der Neuabruf.
    """
    if not aktiv():
        return MARKT_CACHE
    return os.path.join(_datenverzeichnis() or ".", MARKT_CACHE)


def sperr_ablage_datei() -> str:
    """Die SQLite-Datei, in der die Schreibberechtigung (``core/lease.py``) liegt.

    Bei an und gesetztem ``AAA_LEASE_DIR`` eine **gemeinsame** Datei fuer alle Engines: Hinge
    die Sperre am Datenverzeichnis, laege sie bei getrennten Profilen in getrennten Dateien und
    sperrte zwischen ihnen nichts. Nur die Sperre wird geteilt; der uebrige Betriebszustand
    bleibt unter ``zusammenbau.ablage_pfad()`` je Profil.
    """
    from core.state import zusammenbau

    gemeinsam = os.getenv("AAA_LEASE_DIR", "").strip()
    if not aktiv() or not gemeinsam:
        return zusammenbau.ablage_pfad()
    os.makedirs(gemeinsam, exist_ok=True)
    return os.path.join(gemeinsam, zusammenbau.DATEINAME)


def uebernimm_altdatei(alt: str, neu: str) -> None:
    """Kopiert ``alt`` einmal nach ``neu``, wenn ``neu`` fehlt und ``alt`` existiert.

    Wirft nie. Ein Fehler wird bei WARNING gemeldet (CLAUDE.md §5.6); die Engine startet dann
    mit leerer Datei im Profil — das Original bleibt in ``DATA_DIR`` liegen (Muster
    ``core/audit_paths.py::migrate_legacy_audit_log``).
    """
    try:
        if os.path.exists(neu) or not os.path.isfile(alt):
            return
        os.makedirs(os.path.dirname(neu) or ".", exist_ok=True)
        shutil.copy2(alt, neu)
        logger.info("profil_pfade: %s nach %s kopiert (#4631).", alt, neu)
    except Exception as exc:  # noqa: BLE001 — eine fehlende Kopie haelt keine Engine an
        logger.warning(
            "profil_pfade: Altdatei %s nicht nach %s uebernommen (%s) — das Profil "
            "beginnt ohne sie, das Original bleibt unveraendert (#4631).",
            alt,
            neu,
            exc,
        )
