"""#4125 (Plan §13b): Jede Verkaufsstelle im Kern ist eingeordnet — Verkauf → Klasse oder ausgenommen.

Der Test fuehrt die breite Suche aus §13b aus und vergleicht je Datei die Anzahl der Fundstellen
mit der Tabelle unten. Eine neue Fundstelle oder eine neue Datei macht ihn rot, bis jemand sie
einordnet: Bekommt der neue Verkauf eine Klasse aus ``VERKAUFSGRUND_KLASSEN``, oder ist er keiner
(Transport, Stimme, Simulation)? So bleibt „100 % der Verkaeufe haben eine Klasse ≠ unknown“
(Plan §6) pruefbar, auch fuer Code, der nach diesem Plan entsteht.
"""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.vc0

_KERN = Path(__file__).resolve().parents[2] / "core"

#: Die Suche aus Plan §13b.
_MUSTER = re.compile(
    r'action="SELL"|OrderSide\.SELL|side="sell"|=\s*"SELL"|:\s*"SELL"|return "SELL"'
    r'|\("SELL"|2: "SELL"'
    r'|intent_kind="(panic|breaker|strategy_switch|displacement|trim|stop)"'
    r"|close_position\(|close_all_positions\(|OrderClass\.|take_profit|TakeProfit|stop_loss="
)
_AUSSER = re.compile(r'== "SELL"|!= "SELL"')

#: Datei → (Anzahl Fundstellen, Einordnung). Quelle: Plan §13b.
_TABELLE = {
    "engine/schleifen_stops.py": (
        2,
        "Verkauf: Wege 1-7 (analyze_exit ueber position_stop)",
    ),
    "strategies/lstm_strategy.py": (
        4,
        "Verkauf: Wege 8, 9; L779 blockierter Verkauf (HOLD)",
    ),
    "engine/ausstieg_hebel.py": (6, "Verkauf: rotation, book_overflow, trim"),
    "round_table/signal_bau.py": (1, "Verkauf: consensus_sell"),
    "strategies/rl_entscheidung.py": (1, "Verkauf: Weg 9b (RL-Exit-Autoritaet)"),
    "strategies/rl_execution.py": (2, "Verkauf: model_sell (L168), Weg 9b (L256)"),
    "engine/verdraengung.py": (4, "Verkauf: displacement"),
    "engine/absendung_vorlauf.py": (4, "Verkauf: displacement"),
    "engine/signal_desktop_absendung.py": (7, "Verkauf: displacement; L52 Transport"),
    "engine/routes/kapitalpfad.py": (3, "Verkauf: panic"),
    "risk_konto.py": (2, "Verkauf: breaker"),
    "engine/monitor_loop.py": (2, "Verkauf: strategy_switch"),
    "gateway/liquidation.py": (1, "Sammelweg panic/breaker/strategy_switch"),
    "strategies/base.py": (1, "Transport des Strategiepfads (Weg 21, 9b, 14 in 3b)"),
    "engine/broker_stop_pflege.py": (3, "ausgenommen: Platzieren eines Broker-Stops"),
    "engine/absendung_abgang.py": (2, "ausgenommen: Transport (Seite)"),
    "engine/order_aufbau.py": (2, "ausgenommen: Transport (is_exit, TradeRecord)"),
    "adapters/alpaca_broker_adapter.py": (2, "ausgenommen: Definition ohne Aufrufer"),
    "ports/broker_port.py": (2, "ausgenommen: Definition ohne Aufrufer"),
    "alpaca_rate_limiter.py": (2, "ausgenommen: Definition ohne Aufrufer"),
    "simulation.py": (1, "ausgenommen: Backtest-Klient"),
    "simulation_adapter.py": (2, "ausgenommen: Backtest-Klient"),
    "round_table/aufzeichnung.py": (1, "ausgenommen: Agenten-Stimme"),
    "round_table/shadow_specialist_recorder.py": (2, "ausgenommen: Schatten-Stimme"),
    "round_table/shadow_tft_recorder.py": (1, "ausgenommen: Schatten-Stimme"),
    "analysis/shadow_specialist.py": (
        1,
        "ausgenommen: Konstante der Schatten-Auswertung",
    ),
    "telemetry_attrs.py": (1, "ausgenommen: Telemetrie"),
}


def _fundstellen() -> dict:
    gefunden: dict = {}
    for datei in sorted(_KERN.rglob("*.py")):
        rel = datei.relative_to(_KERN).as_posix()
        if rel.startswith("sim/"):
            continue
        for zeile in datei.read_text(encoding="utf-8").splitlines():
            if zeile.lstrip().startswith("#"):
                continue
            if _MUSTER.search(zeile) and not _AUSSER.search(zeile):
                gefunden[rel] = gefunden.get(rel, 0) + 1
    return gefunden


def test_jede_verkaufsstelle_ist_eingeordnet():
    gefunden = _fundstellen()
    erwartet = {datei: anzahl for datei, (anzahl, _) in _TABELLE.items()}
    neu = {d: n for d, n in gefunden.items() if erwartet.get(d) != n}
    weg = {d: n for d, n in erwartet.items() if d not in gefunden}
    assert not neu and not weg, (
        "Verkaufsstellen haben sich geaendert — jede neue Stelle braucht eine Klasse "
        "aus VERKAUFSGRUND_KLASSEN oder eine Begruendung, warum sie kein Verkauf ist "
        f"(Plan §13b). Abweichend: {neu}, verschwunden: {weg}"
    )


def test_die_tabelle_nennt_62_fundstellen():
    assert sum(anzahl for anzahl, _ in _TABELLE.values()) == 62
