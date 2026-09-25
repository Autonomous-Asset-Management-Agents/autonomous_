"""Ableitbarer Idempotenz-Schluessel fuer Broker-Orders (#3387, Epic #3367 ARC-E2).

Der ``client_order_id`` einer Order wird aus der Entscheidung **berechnet**, nicht
gewuerfelt: aus ``decision_id``, dem Leg und einem Versuchszaehler. Damit erzeugt eine
Wiederholung derselben Entscheidung denselben Schluessel — der Broker lehnt das Duplikat
ab, statt eine zweite Order anzulegen.

**Warum eine reine Funktion und kein gespeicherter Schluessel.** Ein gespeicherter
Schluessel haengt daran, dass der Schreibvorgang *vor* dem Absenden fertig war. Genau
dieses Fenster ist das Problem: die Broker-Order-ID wird heute erst *nach* der Rueckmeldung
gesetzt, es gibt also keinen Zwischenstand. Ein gespeicherter Schluessel verschiebt das
Fenster nur. Diese Funktion liest nichts und schreibt nichts; sie ueberlebt jeden Neustart,
und der Reconciler (#3389) kann den erwarteten Schluessel aus der Entscheidung
**nachrechnen** und gegen die Broker-Seite halten, ohne unseren eigenen Speicher zu
befragen. Der Broker bleibt damit die Wahrheit.

**Ein Vokabular.** Das Leg ist ein ``IntentKind`` aus #3378 — kein zweites Woerterbuch
neben den Vertraegen, das auseinanderlaufen koennte.

**Editionsneutral.** Nichts hier liest Konfiguration oder kennt einen Broker-Typ (BORA).
"""

from __future__ import annotations

import hashlib
import re
from typing import get_args

from core.contracts import IntentKind

#: Alpaca nimmt bis zu 128 Zeichen. Die Kappung war bisher nur im HITL-Pfad bekannt
#: (``order_executor.py:2279``) und wird hier zur allgemeinen Eigenschaft.
MAX_CLIENT_ORDER_ID_LEN = 128

#: Das Leg, mit dem eine Entscheidung ohne naehere Angabe an den Broker geht.
DEFAULT_LEG = "entry"

_LEGS = frozenset(get_args(IntentKind))

# Im Rumpf des Schluessels sind nur Buchstaben, Ziffern und Bindestrich zugelassen.
_UNSAFE = re.compile(r"[^A-Za-z0-9-]")

# Der Unterstrich leitet die Digest-Form ein. Weil ``_UNSAFE`` ihn aus jedem woertlichen
# Rumpf entfernt, sind die beiden Formen nachweislich disjunkt — ein gehashter Schluessel
# kann nie zufaellig gleich einem woertlichen sein.
_DIGEST_MARKER = "_"
_DIGEST_LEN = 32


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_DIGEST_LEN]


def derive_client_order_id(
    decision_id: str, leg: str = DEFAULT_LEG, attempt: int = 0
) -> str:
    """Bildet den Idempotenz-Schluessel einer Order.

    Args:
        decision_id: Die Entscheidung, aus der die Order folgt. Sie liegt ohnehin im
            WORM-Datensatz und ist damit nach einem Neustart wieder da.
        leg: Welches Bein derselben Entscheidung — ein ``IntentKind`` aus #3378.
        attempt: Versuchszaehler. Eine **Wiederholung** behaelt ihn; ein bewusst neuer
            Versuch — etwa der Markt-Fallback nach einer abgelehnten oder nicht
            gefuellten Limit-Order — erhoeht ihn und wird dadurch unterscheidbar,
            bleibt aber reproduzierbar.

    Raises:
        ValueError: leere ``decision_id``, unbekanntes Leg oder negativer Versuch.
            Alle drei waeren stille Wege zurueck zu einem nicht wiedererkennbaren
            Schluessel.
    """
    if not decision_id:
        raise ValueError(
            "derive_client_order_id: leere decision_id — ein Schluessel ohne "
            "Entscheidung ist kein Idempotenz-Schluessel."
        )
    if leg not in _LEGS:
        raise ValueError(
            f"derive_client_order_id: unbekanntes Leg {leg!r}. Zulaessig sind die "
            f"IntentKind-Werte aus #3378: {sorted(_LEGS)}"
        )
    if attempt < 0:
        raise ValueError(f"derive_client_order_id: negativer Versuch {attempt}")

    prefix = f"{leg}-{int(attempt)}-"
    rumpf = _UNSAFE.sub("-", decision_id)

    # Die woertliche Form nur, solange sie die Entscheidung vollstaendig und unveraendert
    # traegt. Sonst die Digest-Form: bloss zu kappen wuerde zwei lange, nur am Ende
    # verschiedene Entscheidungen zu einem Schluessel verschmelzen — also genau die
    # Kollision erzeugen, die dieser Schluessel verhindern soll.
    if rumpf == decision_id and len(prefix) + len(rumpf) <= MAX_CLIENT_ORDER_ID_LEN:
        return prefix + rumpf
    return prefix + _DIGEST_MARKER + _digest(decision_id)


#: Erkennt die strukturierte Form ``<leg>-<versuch>-<rumpf>`` wieder.
_STRUCTURED = re.compile(r"^(?P<leg>[a-z_]+)-(?P<attempt>\d+)-(?P<rest>.+)$")

#: Leg-Marke fuer einen Folgeversuch auf einem Schluessel, der die strukturierte Form
#: nicht hat (Bestands-UUID, HITL-Freigabe). Bewusst KEIN ``IntentKind``: das Leg ist
#: aus so einem Schluessel nicht rueckgewinnbar, und das soll man ihm ansehen.
_RETRY_LEG = "retry"


#: Praefix einer Ersatz-Entscheidung (#3429). Kommt eine Order ohne echte ``decision_id``
#: durch, bauen die Aufrufer eine Ersatz-ID aus Art, Nutzer und Symbol — damit der Datensatz
#: nicht leer bleibt. Eine solche ID ist aber NICHT eindeutig je Entscheidung: zwei
#: Verdraengungen desselben Symbols truegen dieselbe. Aus ihr darf kein Idempotenz-Schluessel
#: werden, sonst hielte der Broker die zweite Order fuer ein Duplikat der ersten.
ERSATZ_PRAEFIX = "ersatz-"


def ersatz_decision_id(art: str, wer: "str | None", symbol: str) -> str:
    """Die Ersatz-ID fuer eine Order ohne echte Entscheidung — erkennbar als solche."""
    return f"{ERSATZ_PRAEFIX}{art}-{wer or 'global'}-{symbol}"


def ist_ersatz(decision_id: "str | None") -> bool:
    """``True``, wenn ``decision_id`` eine Ersatz-ID ist und keine echte Entscheidung."""
    return isinstance(decision_id, str) and decision_id.startswith(ERSATZ_PRAEFIX)


def decision_id_aus(client_order_id: str) -> "str | None":
    """Die Entscheidung, aus der ``client_order_id`` abgeleitet wurde — oder ``None`` (#3449).

    Die Umkehrung von ``derive_client_order_id``, soweit sie moeglich ist: In der
    woertlichen Form ``<leg>-<versuch>-<decision_id>`` steckt die Entscheidung unveraendert.
    In der Digest-Form (``…-_<32 Hex>``) ist sie nicht rueckgewinnbar — dann ``None``, nie
    eine geratene Entscheidung.

    Gebraucht dort, wo eine Anfrage ohne ``decision_id`` ankommt, ihr Schluessel sie aber
    traegt. Ein Ersatzschluessel an dieser Stelle liesse den Neustart einen ANDEREN
    Schluessel ableiten — und der Broker naehme zwei Orders an (CH-4, so gemessen).
    """
    if not isinstance(client_order_id, str) or not client_order_id:
        return None
    m = _STRUCTURED.match(client_order_id)
    if not m or m.group("leg") not in _LEGS:
        return None
    rest = m.group("rest")
    if rest.startswith(_DIGEST_MARKER) and len(rest) == 1 + _DIGEST_LEN:
        return None
    return rest


def next_attempt(client_order_id: str) -> str:
    """Der Schluessel des naechsten Versuchs desselben Legs.

    Gedacht fuer die beiden Markt-Fallbacks im Exit-Pfad (``order_executor.py:981``
    und ``:1882``). Sie schicken eine **zweite** Order fuer dasselbe Leg; bisher mit
    ``str(uuid.uuid4())``, womit der Broker sie nicht als Folgeversuch einordnen kann.

    Bei der strukturierten Form bleibt der Entscheidungsanteil zeichengleich erhalten und
    nur der Versuch steigt. Bei einer Alt-Form (HITL-Freigabe, Bestands-UUID) ist der
    Entscheidungsanteil nicht rueckgewinnbar — dann wird deterministisch aus dem
    urspruenglichen Schluessel abgeleitet, damit der Folgeversuch wenigstens bestimmt und
    unterscheidbar ist.
    """
    if not client_order_id:
        raise ValueError("next_attempt: leerer client_order_id")

    m = _STRUCTURED.match(client_order_id)
    if m and m.group("leg") in _LEGS:
        return (f"{m.group('leg')}-{int(m.group('attempt')) + 1}-{m.group('rest')}")[
            :MAX_CLIENT_ORDER_ID_LEN
        ]

    return f"{_RETRY_LEG}-1-{_DIGEST_MARKER}{_digest(client_order_id)}"


def derive_approval_client_order_id(approval_id: str) -> str:
    """Die Freigabeform — **zeichengleich** zu ``f"hitl-{approval_id}"[:128]``.

    Diese Form ist bereits heute eine Ableitung (``order_executor.py:2279``): die
    Freigabe-ID ist an dieser Stelle die stabile Identitaet der Entscheidung. Sie bleibt
    unveraendert, weil Freigaben, die vor der Umstellung in die Warteschlange gelegt
    wurden, ihre ``approval_id`` bereits tragen — eine abweichende Ableitung erzeugte am
    Umstellungstag je offener Freigabe einen zweiten Schluessel, also genau ein Duplikat.
    """
    if not approval_id:
        raise ValueError("derive_approval_client_order_id: leere approval_id")
    return f"hitl-{approval_id}"[:MAX_CLIENT_ORDER_ID_LEN]
