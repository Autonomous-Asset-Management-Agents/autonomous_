#!/usr/bin/env python3
"""#3588 (ARC-E2.16) — misst den Ereignisstrom des Abgleichs am Paper-Konto.

Beantwortet vier Fragen, die Plan #3389 §8 offen gelassen hat:

1. Wie lange dauert es vom Broker-Fill bis zur Kenntnis der Engine, ueber den Strom?
2. Was passiert bei einem Verbindungsabbruch: kehrt der Strom von selbst zurueck?
3. Wird ein Fill aus der Trennung nachgeliefert, oder findet ihn erst der periodische Lauf?
4. Ist eine Trennung sichtbar?

Der Aufbau faehrt **nicht** die Engine: er baut den ``ReconciliationService`` direkt auf
(die Naht liegt aussen, genau dafuer) und kauft dabei zwei Mal ein Stueck ``SYMBOL``, die
am Ende wieder glattgestellt werden.

Schluessel kommen aus der Umgebung (``ALPACA_API_KEY``/``ALPACA_SECRET_KEY``); fehlen sie,
greift das Skript auf die Projekt-Konfiguration zurueck (``.env.oss`` bzw. Schluesselbund) —
gemessen am 23.09.2026 weist Alpaca die dort hinterlegten Schluessel am Paper-Konto aber mit
401 ab, es braucht also die Schluessel des Paper-Kontos in der Umgebung. Das Skript
verweigert jeden Endpunkt ausser ``paper-api.alpaca.markets`` und laeuft nur bei offenem
Markt.

    python scripts/mess_ereignisstrom.py --ausgabe docs/3588-ereignisstrom/results/messung.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_AI_BOT = Path(__file__).resolve().parents[1]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

PAPER = "https://paper-api.alpaca.markets"


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


async def _warte_auf(bedingung, grenze_s: float, takt_s: float = 0.2) -> bool:
    """Wartet, bis ``bedingung()`` wahr ist. Liefert False beim Zeitablauf."""
    ende = time.monotonic() + grenze_s
    while time.monotonic() < ende:
        if bedingung():
            return True
        await asyncio.sleep(takt_s)
    return bool(bedingung())


async def messe(args) -> dict:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest
    from alpaca.trading.stream import TradingStream

    from core.reconciliation import ReconciliationService

    key, secret = _schluessel()
    client = TradingClient(key, secret, paper=True)

    befund: dict = {"issue": 3588, "symbol": args.symbol, "start": _jetzt()}
    gekauft = 0

    # --- Aufbau: Dienst plus echter Strom ---------------------------------------------
    dienst = ReconciliationService(client, None)
    ereignisse: list = []
    echtes_on_broker_fill = dienst.on_broker_fill

    def _mitschrift(order, *, decision_id="", source="stream"):
        ergebnis = echtes_on_broker_fill(order, decision_id=decision_id, source=source)
        if ergebnis is not None:
            ereignisse.append(
                {
                    "zeit": _jetzt(),
                    "monoton": time.monotonic(),
                    "quelle": source,
                    "order": str(getattr(order, "id", "")),
                    "menge": float(getattr(order, "filled_qty", 0) or 0),
                }
            )
        return ergebnis

    dienst.on_broker_fill = _mitschrift

    def _fabrik():
        return TradingStream(key, secret, paper=True)

    # Start-Abgleich wie in der Engine: der erste Lauf UEBERNIMMT den Bestand. Ohne ihn
    # waere der Lauf am Ende ebenfalls ein Uebernahme-Lauf und meldete nie einen
    # entgangenen Fill — die Messung haette die Frage gar nicht stellen koennen.
    satz_start = await dienst.run_once()
    befund["start_abgleich"] = {
        "orders": satz_start.broker_orders,
        "positionen": satz_start.broker_positions,
    }

    t_abo = time.monotonic()
    dienst.start_fill_stream(_fabrik)
    strom = dienst._stream
    befund["strom_abonniert"] = strom is not None
    if strom is None:
        befund["ergebnis"] = "Strom nicht abonniert — Messung nicht moeglich"
        return befund

    verbunden = await _warte_auf(lambda: getattr(strom, "_running", False), 20.0)
    befund["strom_verbunden"] = bool(verbunden)
    befund["sekunden_bis_verbindung"] = (
        round(time.monotonic() - t_abo, 3) if verbunden else None
    )

    def _kaufe(marke: str):
        anfrage = MarketOrderRequest(
            symbol=args.symbol,
            qty=1,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            client_order_id=f"mess-3588-{marke}-{int(time.time())}",
        )
        t0 = time.monotonic()
        abgesendet = _jetzt()
        order = client.submit_order(anfrage)
        return order, t0, abgesendet

    # --- Phase A: der Strom laeuft -----------------------------------------------------
    vorher = len(ereignisse)
    order_a, t0_a, abgesendet_a = _kaufe("a")
    gekauft += 1
    gemeldet = await _warte_auf(lambda: len(ereignisse) > vorher, args.fill_timeout)
    befund["phase_a_strom_laeuft"] = {
        "order": str(order_a.id),
        "abgesendet": abgesendet_a,
        "ueber_strom_gemeldet": bool(gemeldet),
        "sekunden_bis_meldung": (
            round(ereignisse[-1]["monoton"] - t0_a, 3) if gemeldet else None
        ),
        "quelle": ereignisse[-1]["quelle"] if gemeldet else None,
    }

    # --- Phase B: Verbindung abreissen -------------------------------------------------
    # Die Trennung wird HALTEND erzeugt, indem die Empfangsschleife endet — nicht, indem
    # der Socket im Takt zugeschlagen wird. Gemessen am 23.09.2026: Das wiederholte
    # Schliessen laeuft in Alpacas Verbindungsgrenze (HTTP 429), und der Strom braucht
    # danach knapp zwei Minuten zurueck. Das misst dann die Grenze, nicht den Ausfall.
    trennung_t0 = time.monotonic()
    await dienst.stop_fill_stream()
    befund["phase_b_trennung"] = {
        "getrennt": _jetzt(),
        "trennung_gehalten_s": args.trennung_sekunden,
    }
    vorher = len(ereignisse)
    order_b, t0_b, abgesendet_b = _kaufe("b")
    gekauft += 1
    await asyncio.sleep(args.trennung_sekunden)
    dienst.start_fill_stream(_fabrik)
    strom = dienst._stream
    zurueck = await _warte_auf(
        lambda: getattr(strom, "_running", False), args.reconnect_timeout
    )
    nachgeliefert = await _warte_auf(
        lambda: len(ereignisse) > vorher, args.fill_timeout
    )
    befund["phase_b_trennung"].update(
        {
            "order": str(order_b.id),
            "strom_kehrt_zurueck": bool(zurueck),
            "sekunden_bis_rueckkehr": (
                round(time.monotonic() - trennung_t0, 3) if zurueck else None
            ),
            "abgesendet": abgesendet_b,
            "fill_nachgeliefert_ueber_strom": bool(nachgeliefert),
            "sekunden_bis_meldung": (
                round(ereignisse[-1]["monoton"] - t0_b, 3) if nachgeliefert else None
            ),
        }
    )

    # --- Phase C: was findet der periodische Lauf? -------------------------------------
    satz = await dienst.run_once()
    befund["phase_c_periodischer_lauf"] = {
        "hinweis": "kein Uebernahme-Lauf — der Start-Abgleich lief oben",
        "orders_beim_broker": satz.broker_orders,
        "positionen_beim_broker": satz.broker_positions,
        "befunde": [
            {"art": b.kind, "symbol": b.symbol, "detail": b.detail} for b in satz.breaks
        ],
        "sauber": satz.clean,
    }
    befund["ereignisse"] = ereignisse

    # --- Aufraeumen --------------------------------------------------------------------
    await dienst.stop_fill_stream()
    if gekauft:
        glatt = client.submit_order(
            MarketOrderRequest(
                symbol=args.symbol,
                qty=gekauft,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
                client_order_id=f"mess-3588-glatt-{int(time.time())}",
            )
        )
        befund["glattstellung"] = {"id": str(glatt.id), "qty": gekauft}
    befund["ende"] = _jetzt()
    return befund


def _schluessel() -> tuple:
    """Schluessel aus der Umgebung; sonst aus der Projekt-Konfiguration.

    Die Konfiguration laedt sie aus ``.env.oss`` bzw. dem Schluesselbund — derselbe Weg,
    den die Engine geht. Damit muss niemand ein Geheimnis in die Kommandozeile tippen.
    Der Paper-Zwang bleibt: ``TradingClient(..., paper=True)`` spricht nur das Paper-Konto
    an, ein Live-Schluessel wird dort abgewiesen.
    """
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if key and secret and key != "..." and secret != "...":
        return key, secret
    try:
        import config

        cfg = config.get_config()
        roh_key = getattr(cfg, "ALPACA_API_KEY", None)
        roh_secret = getattr(cfg, "ALPACA_SECRET_KEY", None)
        key = str(getattr(roh_key, "get_secret_value", lambda: roh_key)() or "")
        secret = str(
            getattr(roh_secret, "get_secret_value", lambda: roh_secret)() or ""
        )
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"Konfiguration nicht lesbar: {exc}\n")
        return "", ""
    return key, secret


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbol", default="SPY")
    p.add_argument("--ausgabe", default=None)
    p.add_argument("--fill-timeout", type=float, default=60.0)
    p.add_argument("--reconnect-timeout", type=float, default=60.0)
    p.add_argument(
        "--trennung-sekunden",
        type=float,
        default=0.0,
        dest="trennung_sekunden",
        help="Die Trennung so lange halten (sonst verbindet der Strom in ~1 s neu).",
    )
    args = p.parse_args(argv)

    key, secret = _schluessel()
    if not key or not secret:
        sys.stderr.write(
            "Keine Paper-Schluessel gefunden: weder ALPACA_API_KEY/ALPACA_SECRET_KEY in "
            "der Umgebung noch in der Projekt-Konfiguration (.env.oss / Schluesselbund).\n"
        )
        return 2

    from alpaca.common.exceptions import APIError
    from alpaca.trading.client import TradingClient

    pruef = TradingClient(key, secret, paper=True)
    url = getattr(pruef, "_base_url", "")
    basis = str(getattr(url, "value", url))
    if basis.rstrip("/") != PAPER:
        sys.stderr.write(f"Verweigert: Endpunkt {basis!r} ist nicht das Paper-Konto.\n")
        return 2
    try:
        uhr = pruef.get_clock()
    except APIError as exc:
        # Kein Traceback fuer den haeufigsten Fall: falsche oder fehlende Schluessel.
        if getattr(exc, "status_code", None) == 401:
            sys.stderr.write(
                "Alpaca weist die Schluessel ab (401). Es muessen die Schluessel des "
                "PAPER-Kontos sein; Platzhalter wie '...' sind keine.\n"
            )
            return 2
        sys.stderr.write(f"Alpaca antwortet nicht: {exc}\n")
        return 2
    if not uhr.is_open:
        sys.stderr.write("Markt geschlossen — ohne Fill ist die Messung wertlos.\n")
        return 3

    befund = asyncio.run(messe(args))
    text = json.dumps(befund, indent=2, ensure_ascii=False)
    print(text)
    if args.ausgabe:
        os.makedirs(os.path.dirname(args.ausgabe) or ".", exist_ok=True)
        with open(args.ausgabe, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
