"""#4274 (H-4a) — Charakterisierungsnetz fuer ``run_round_table``, vor dem Umbau H-4c bis H-4h.

Plan: ``docs/4274-*/implementation_plan.md``. Schnitt: ``docs/3738-arc-e6-gestalt/
H4_SCHNITT_round_table_runner.md`` (§3 Zugriffsregel, §4 Netz, §6 Beobachtung).

Der echte Dirigent laeuft mit echter ``ConsensusEngine``, echtem
``ComplianceGatekeeper``, echtem Strict-ML-Gate, Agent-Veto, Meta-Label-Gate,
Signalbau, Senate-Serialisierung und echten Zaehlern. Ersetzt sind nur die
Aussengrenzen: ``vote`` der Agenten, der Senate-Logger, die Strategie-Registry, der
ML-Watchdog, die Uhr hinter ``CompositionRoot``, die Redis-Gewichte der Agenten und —
im Meta-Label-Szenario — das Meta-Label-Modell.

Zugriffsregel (Entscheidung §3, Weg a): Am Kern gepatcht werden nur Namen, die im Kern
bleiben und vom Kern gelesen werden (``_senate``, ``_active_agents``,
``_consensus_engine``, ``_gatekeeper``, ``_ml_watchdog``, ``get_global_registry``,
``_bump_run``, ``_bump_agent_failure``, ``run_round_table``) und
``_record_consensus_outcome`` (Szenario ``beobachtung_wirft``, Plan §7).

Hermetik: Jede Netzverbindung waehrend eines Laufs laesst ``fahre`` scheitern — auch
wenn ein Agent oder ein fail-safe-Haken die Ausnahme verschluckt.

Referenz neu schreiben (nur auf unveraendertem ``core/``, der Diff ist der Befund):

    cd ai_trading_bot && python tests/unit/_round_table_charakterisierung.py --schreibe
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import json
import logging
import re
import socket
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

_AI_BOT = Path(__file__).resolve().parents[2]
if str(_AI_BOT) not in sys.path:
    sys.path.insert(0, str(_AI_BOT))

REFERENZ = _AI_BOT / "tests" / "fixtures" / "round_table_charakterisierung_h4a.json"

JETZT = datetime(2026, 10, 6, 14, 30, tzinfo=timezone.utc)
SYMBOL = "NVDA"

# Ausgelieferte Werte aus ``settings.py`` (Zeile in Klammern), fest statt aus der
# Umgebung, damit kein gesetztes Env das Netz verschiebt.
VORGABE = {
    "IMPLIED_VOL_FORECAST_ENABLED": True,  # 424
    "IV_VRP_DEBIAS_FACTOR": 0.85,  # 440
    "AGENT_VOTE_TIMEOUT_SECONDS": 60.0,  # 991
    "SYMBOL_EVAL_TIMEOUT_SECONDS": 120.0,  # 994
    "CYCLE_TIMEOUT_SECONDS": 1800.0,  # 997
    "SHADOW_TFT_VOTE_ENABLED": False,  # 1110
    "SHADOW_SPECIALIST_VOTE_ENABLED": False,  # 1121
    "GATEKEEPER_REQUIRE_CONTEXT": False,  # 1493
    "GATEKEEPER_STRICT_ML_REQUIRES_RL": False,  # 1524
    "DRAWDOWN_GUARD_CONDITIONER_ENABLED": False,  # 1587
    "DRAWDOWN_CONVICTION_DAMP_MAX": 0.8,  # 1593
    "META_LABEL_FILTER_ENABLED": False,  # 1677
    "META_LABEL_REQUIRE_MODEL": False,  # 1696
    "REGIME_CONDITIONER_ENABLED": True,  # 1725
    "REGIME_RISKOFF_DAMP_MAX": 0.5,  # 1733
    "ROUND_TABLE_DISTINCT_ML_SOURCES": True,  # 2371
    "DECISION_CAPTURE_ENABLED": True,  # 2432
    "CONSENSUS_RETURN_HARNESS_PATH": "",  # 69
}
# Beim Import von ``consensus.py`` gelesen (``settings.py`` 1915/1918) — nicht
# nachtraeglich setzbar. Weicht die Umgebung ab, scheitert ``fahre`` laut.
SCHWELLEN = (0.73, 0.35)

# Reihenfolge = Index in ``_active_agents``. Unsymmetrisch um die ML-Stimmen, damit eine
# vertauschte Zuordnung Stimme→Agent (i ↔ n-1-i) andere Namen trifft.
AGENTEN = (
    "DrawdownGuardAgent",
    "RegimeDetectionAgent",
    "MomentumAgent",
    "VIXAwareRiskAgent",
    "LSTMSignalAgent",
    "RLConfidenceAgent",
    "NewsSentimentAgent",
)

# Log-Zeilen des Kerns, die das Netz festhaelt (Entscheidung §6).
LOG_PRAEFIXE = (
    "VOTE[",
    "MIFID_AUDIT[",
    "AI_SECURITY[runner]",
    "ComplianceGatekeeper: VETO",
    "RoundTable[",
    "run_round_table:",
)

GRUNDZUSTAND = {
    "symbol": SYMBOL,
    "ohlc": {
        "open": 118.0,
        "high": 121.5,
        "low": 117.2,
        "close": 120.4,
        "volume": 2_500_000.0,
    },
    "current_time": "2026-10-06T14:30:00+00:00",
    "vix": 18.5,
    "regime": "normal",
    "implied_vol": 0.42,
    "position_context_confirmed": True,
    "in_position": True,
    "position_qty": 10.0,
    "position_avg_price": 112.0,
    "unrealized_pnl": 84.0,
    "signal": None,
    "error": None,
    "round_table_scores": None,
    "consensus_ranking": None,
}


# ── Szenarien ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Stimme:
    """Eine gueltige Stimme; ``abstain`` macht daraus einen ``SignalCandidate``."""

    score: Optional[float]
    weight: float
    vetoed: bool = False
    abstain: Optional[str] = None


@dataclass(frozen=True)
class Wirft:
    """``vote`` wirft eine frische Ausnahme dieses Typs."""

    typ: type
    text: str = ""


@dataclass(frozen=True)
class Szenario:
    name: str
    # Agentenname → Stimme | Wirft | None | Funktion(state) -> Ergebnis
    stimmen: dict
    flags: dict = field(default_factory=dict)  # ueberschreibt VORGABE
    ohne_boot: bool = False
    beobachtung_wirft: bool = False
    meta_label_ohne_edge: bool = False


_BUY = {
    "DrawdownGuardAgent": Stimme(0.9, 0.6),
    # 0.4: risk-off-Konditionierung greift (#3618), BUY bleibt BUY.
    "RegimeDetectionAgent": Stimme(0.4, 0.5),
    "MomentumAgent": Stimme(0.85, 1.0),
    "VIXAwareRiskAgent": Stimme(0.8, 0.45),
    "LSTMSignalAgent": Stimme(0.9, 1.5),
    "RLConfidenceAgent": Stimme(0.82, 1.2),
    "NewsSentimentAgent": Stimme(0.75, 0.8),
}
_SELL = {
    "DrawdownGuardAgent": Stimme(0.8, 0.6),
    "RegimeDetectionAgent": Stimme(0.5, 0.5),
    "MomentumAgent": Stimme(0.15, 1.0),
    "VIXAwareRiskAgent": Stimme(0.3, 0.45),
    "LSTMSignalAgent": Stimme(0.1, 1.5),
    "RLConfidenceAgent": Stimme(0.2, 1.2),
    "NewsSentimentAgent": Stimme(0.25, 0.8),
}
_HOLD = {
    "DrawdownGuardAgent": Stimme(0.8, 0.6),
    "RegimeDetectionAgent": Stimme(0.55, 0.5),
    "MomentumAgent": Stimme(0.5, 1.0),
    "VIXAwareRiskAgent": Stimme(0.55, 0.45),
    "LSTMSignalAgent": Stimme(0.6, 1.5),
    "RLConfidenceAgent": Stimme(0.45, 1.2),
    "NewsSentimentAgent": Stimme(0.5, 0.8),
}


def _mit(basis: dict, **ersetzt) -> dict:
    return {**basis, **ersetzt}


SZENARIEN: tuple[Szenario, ...] = (
    Szenario("buy", _BUY),
    Szenario("sell", _SELL),
    Szenario("hold", _HOLD),
    Szenario("strict_ml_block", _mit(_BUY, LSTMSignalAgent=Stimme(0.5, 0.0))),
    Szenario(
        "lstm_abgeschaltet",
        _mit(_BUY, LSTMSignalAgent=Stimme(None, 0.0, abstain="DISABLED")),
    ),
    Szenario(
        "agent_veto_buy", _mit(_BUY, DrawdownGuardAgent=Stimme(0.2, 0.6, vetoed=True))
    ),
    Szenario(
        "agent_veto_sell_geht_durch",
        _mit(_SELL, DrawdownGuardAgent=Stimme(0.2, 0.6, vetoed=True)),
    ),
    Szenario(
        "meta_label_downgrade",
        _BUY,
        flags={"META_LABEL_FILTER_ENABLED": True},
        meta_label_ohne_edge=True,
    ),
    Szenario(
        "timeout_ml_stimme", _mit(_BUY, RLConfidenceAgent=Wirft(asyncio.TimeoutError))
    ),
    Szenario(
        "agenten_ausnahme",
        _mit(
            _BUY,
            NewsSentimentAgent=Wirft(
                RuntimeError, "Nachrichtenquelle ausgefallen (Fixture)"
            ),
        ),
    ),
    Szenario("none_stimme", _mit(_BUY, VIXAwareRiskAgent=None)),
    Szenario(
        "alle_ausgefallen",
        {name: Wirft(RuntimeError, "Ausfall (Fixture)") for name in AGENTEN},
    ),
    Szenario(
        "integritaetswarnung",
        {
            name: Stimme(0.85, w.weight)
            for name, w in _BUY.items()
            if isinstance(w, Stimme)
        },
    ),
    Szenario("fehlender_boot", _BUY, ohne_boot=True),
    Szenario("beobachtung_wirft", _BUY, beobachtung_wirft=True),
    # Wie ``beobachtung_wirft``, aber mit Timeout und Ausnahme: erst dann erreicht der
    # Dirigent die Schutzklammern um ``_bump_agent_failure`` in beiden Zweigen.
    Szenario(
        "beobachtung_wirft_bei_ausfall",
        _mit(
            _BUY,
            RLConfidenceAgent=Wirft(asyncio.TimeoutError),
            NewsSentimentAgent=Wirft(
                RuntimeError, "Nachrichtenquelle ausgefallen (Fixture)"
            ),
        ),
        beobachtung_wirft=True,
    ),
)


def szenario(sz) -> Szenario:
    if isinstance(sz, Szenario):
        return sz
    return next(s for s in SZENARIEN if s.name == sz)


# ── Aussengrenzen ────────────────────────────────────────────────────────────


def _stimme_bauen(agent_name: str, spec) -> Callable[[dict], Any]:
    """Ein frisches Ergebnis je Aufruf: der Dirigent setzt ``vetoed`` auf den Stimmen."""
    from core.contracts.signal_candidate import AbstainReason, SignalCandidate
    from core.round_table.base_agent import VoteResult

    def _vote(state):
        if spec is None:
            return None
        if isinstance(spec, Wirft):
            raise spec.typ(spec.text) if spec.text else spec.typ()
        if callable(spec):
            return spec(state)
        reasoning = f"{agent_name}: Fixture-Begruendung"
        if spec.abstain is not None:
            return SignalCandidate(
                agent_name=agent_name,
                symbol=state["symbol"],
                score=None,
                weight=spec.weight,
                abstain_reason=AbstainReason(spec.abstain),
                reasoning=reasoning,
            )
        return VoteResult(
            agent_name=agent_name,
            symbol=state["symbol"],
            score=spec.score,
            weight=spec.weight,
            reasoning=reasoning,
            vetoed=spec.vetoed,
        )

    return _vote


class _FesteUhr:
    def now(self) -> datetime:
        return JETZT

    def time(self) -> float:
        return JETZT.timestamp()


class _Watchdog:
    def __init__(self):
        self.aufrufe: list = []

    def record_error(self, agent_name, exc):
        self.aufrufe.append(["record_error", agent_name, type(exc).__name__])

    def record_success(self, agent_name):
        self.aufrufe.append(["record_success", agent_name])


class _Registry:
    """Strategie-Registry ohne aktive Strategie."""

    def get_active(self):
        return None

    def get(self, name):
        return None


class _KeinRedis:
    """Agenten-Gewichte: kein Eintrag in ``agent_weights_v2`` → ``default_weight``."""

    def hget(self, *args, **kwargs):
        return None


class _MetaLabelOhneEdge:
    def should_trade(self, state, votes, score):
        return False, 0.31, "p=0.31 < 0.55 (no edge net-of-cost)"


class _Mitschrift(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.zeilen: list = []

    def emit(self, record):
        text = record.getMessage()
        if text.startswith(LOG_PRAEFIXE):
            self.zeilen.append(f"{record.levelname} {text}")


# Spaete Importe auf dem Pfad (Entscheidung §3). Vor dem Riegel geladen, damit kein
# Import-Nebeneffekt (``cloud_logger`` startet einen Hintergrund-Thread) im Lauf liegt.
_LAZY = (
    "core.cloud_logger",
    "core.decision_capture.capture",
    "core.engine.time_budget",
    "core.events",
    "core.ml.vol_model",
    "core.options_skew",
    "core.risk_manager",
    "core.round_table.consensus_return_recorder",
    "core.round_table.meta_label",
)


def _wirft(*args, **kwargs):
    raise RuntimeError("Beobachtung kaputt (Fixture)")


# ── Lauf ─────────────────────────────────────────────────────────────────────


def fahre(sz, *, dirigent=None) -> dict:
    """Ein Szenario durch den echten Dirigenten; kanonisches Ergebnis als dict."""
    import config
    import core.round_table.consensus as consensus
    import core.round_table.meta_label as meta_label
    from core.composition.root import CompositionRoot
    from core.redis_client import RedisClient
    from core.round_table import runner
    from core.round_table.recent_decisions import (
        clear_recent_round_table_decisions,
        get_round_table_decision,
    )

    sz = szenario(sz)
    ist_schwellen = (consensus.SIGNAL_BUY_THRESHOLD, consensus.SIGNAL_SELL_THRESHOLD)
    if ist_schwellen != SCHWELLEN:
        raise AssertionError(
            f"Signal-Schwellen {ist_schwellen} != ausgeliefert {SCHWELLEN} — "
            "SIGNAL_BUY_THRESHOLD/SIGNAL_SELL_THRESHOLD in der Umgebung gesetzt?"
        )

    agenten = {type(a).__name__: a for a in runner.ALL_AGENTS}
    aktiv = [agenten[name] for name in AGENTEN]
    watchdog = _Watchdog()
    senate = MagicMock()
    senate.log_session = AsyncMock(return_value=None)
    mitschrift = _Mitschrift()
    kern_logger = logging.getLogger(runner.__name__)
    netzzugriffe: list = []

    echtes_connect = socket.socket.connect

    def _verbinde(sock, adresse, *args, **kwargs):
        # Unter Windows baut jede neue Ereignisschleife ihr Socket-Paar per ``connect``
        # auf localhost (``socket._fallback_socketpair``) — kein Aussenzugriff.
        if sys._getframe(1).f_code.co_name == "_fallback_socketpair":
            return echtes_connect(sock, adresse, *args, **kwargs)
        netzzugriffe.append(repr(adresse))
        raise AssertionError(f"Netzzugriff im Netz: {adresse!r}")

    for name in _LAZY:
        importlib.import_module(name)
    schleife = asyncio.new_event_loop()
    try:
        with pytest.MonkeyPatch.context() as mp:
            cfg = config.get_config()
            for key, wert in {**VORGABE, **sz.flags}.items():
                mp.setattr(cfg, key, wert, raising=False)

            mp.setattr(runner, "_consensus_engine", consensus.ConsensusEngine())
            mp.setattr(runner, "_gatekeeper", runner.ComplianceGatekeeper())
            mp.setattr(runner, "_senate", senate)
            mp.setattr(runner, "_active_agents", aktiv)
            mp.setattr(runner, "_ml_watchdog", watchdog)
            mp.setattr(runner, "get_global_registry", lambda: _Registry())
            if sz.ohne_boot:
                mp.setattr(runner, "_consensus_engine", None)
            if sz.beobachtung_wirft:
                mp.setattr(runner, "_bump_run", _wirft)
                mp.setattr(runner, "_bump_agent_failure", _wirft)
                mp.setattr(runner, "_record_consensus_outcome", _wirft)
            if dirigent is not None:
                mp.setattr(runner, "run_round_table", dirigent)
            for name, agent in zip(AGENTEN, aktiv):
                mp.setattr(
                    agent,
                    "vote",
                    AsyncMock(side_effect=_stimme_bauen(name, sz.stimmen.get(name))),
                )
            if sz.meta_label_ohne_edge:
                mp.setattr(meta_label, "get_meta_label_filter", _MetaLabelOhneEdge)

            mp.setattr(CompositionRoot.get_instance(), "clock_port", _FesteUhr())
            mp.setattr(RedisClient, "get_sync_redis", lambda *a, **k: _KeinRedis())
            mp.setattr(socket.socket, "connect", _verbinde)
            mp.setattr(socket.socket, "connect_ex", _verbinde)
            mp.setattr(kern_logger, "level", logging.DEBUG)
            mp.setattr(kern_logger, "disabled", False)
            kern_logger.addHandler(mitschrift)
            try:
                runner.reset_decision_counters()
                clear_recent_round_table_decisions()
                ergebnis = schleife.run_until_complete(
                    runner.run_round_table(dict(GRUNDZUSTAND))
                )
                offen = asyncio.all_tasks(schleife)
                if offen:
                    schleife.run_until_complete(asyncio.gather(*offen))
                zaehler = runner.get_decision_counters()
                aufgezeichnet = get_round_table_decision(SYMBOL) is not None
            finally:
                kern_logger.removeHandler(mitschrift)
                runner.reset_decision_counters()
                clear_recent_round_table_decisions()
    finally:
        schleife.close()

    if netzzugriffe:
        raise AssertionError(f"[{sz.name}] Netzzugriff im Netz: {netzzugriffe}")

    sitzungen = [
        dataclasses.asdict(aufruf.args[0])
        for aufruf in senate.log_session.call_args_list
    ]
    signal = ergebnis.get("signal")
    return _kanonisch_dict(
        {
            "rueckgabe": {
                "schluessel": sorted(ergebnis),
                "error": ergebnis.get("error"),
                "consensus_ranking": ergebnis.get("consensus_ranking"),
                "round_table_scores": ergebnis.get("round_table_scores"),
                "session_id": ergebnis.get("session_id"),
                "regime_conditioning": ergebnis.get("regime_conditioning"),
            },
            "signal": (
                None
                if signal is None
                else {
                    "symbol": signal.symbol,
                    "action": signal.action,
                    "suggested_quantity": signal.suggested_quantity,
                    "is_simulation": signal.is_simulation,
                }
            ),
            # ``decision_time`` ist Wanduhr (``cloud_logger.DecisionContext``).
            "decision_context": (
                None
                if signal is None
                else signal.decision_context.model_dump(
                    mode="json", exclude={"decision_time"}
                )
            ),
            "senate": sitzungen,
            "zaehler": zaehler,
            "watchdog": watchdog.aufrufe,
            "anzeige_aufgezeichnet": aufgezeichnet,
            "log": mitschrift.zeilen,
        }
    )


# ── Kanonisierung, Referenz, Vergleich ───────────────────────────────────────

_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


def _runde(wert):
    """Floats auf 9 Stellen; alles andere JSON-tauglich."""
    if isinstance(wert, bool) or wert is None:
        return wert
    if isinstance(wert, float):
        return round(float(wert), 9)
    if isinstance(wert, dict):
        return {str(k): _runde(v) for k, v in wert.items()}
    if isinstance(wert, (list, tuple)):
        return [_runde(v) for v in wert]
    if isinstance(wert, datetime):
        return wert.isoformat()
    return wert


def kanonisch(wert) -> str:
    """JSON mit sortierten Schluesseln; UUIDs nach erstem Auftreten ``<id-1>``, ``<id-2>``…

    So bleibt die Referenz deterministisch und haelt trotzdem fest, welche Felder
    denselben Wert tragen (``SenateSession.decision_id`` = ``decision_context.decision_id``).
    """
    text = json.dumps(_runde(wert), sort_keys=True, default=str, ensure_ascii=False)
    nummern: dict = {}

    def _ersetze(treffer):
        uuid = treffer.group(0).lower()
        nummern.setdefault(uuid, f"<id-{len(nummern) + 1}>")
        return nummern[uuid]

    return _UUID.sub(_ersetze, text)


def _kanonisch_dict(wert) -> dict:
    return json.loads(kanonisch(wert))


def messe_alle(szenarien=SZENARIEN) -> dict:
    return {sz.name: fahre(sz) for sz in szenarien}


def lade_referenz() -> dict:
    return json.loads(REFERENZ.read_text(encoding="utf-8"))


def schreibe_referenz(ist: dict) -> None:
    REFERENZ.write_text(
        json.dumps(ist, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# Kausale Reihenfolge: was abgestimmt wurde, was daraus wurde, was beobachtet wurde.
_REIHENFOLGE = (
    "rueckgabe",
    "senate",
    "signal",
    "decision_context",
    "zaehler",
    "watchdog",
    "anzeige_aufgezeichnet",
    "log",
)


def _schluessel(k: str) -> tuple:
    return (_REIHENFOLGE.index(k) if k in _REIHENFOLGE else len(_REIHENFOLGE), k)


def befunde(referenz, ist, pfad: str = "") -> list[str]:
    """Pfad-genaue Abweichungen, z. B. ``senate[0].votes[2].vetoed: referenz=true ist=false``."""
    if isinstance(referenz, dict) and isinstance(ist, dict):
        out: list[str] = []
        for k in sorted(set(referenz) | set(ist), key=_schluessel):
            p = f"{pfad}.{k}" if pfad else str(k)
            if k not in referenz:
                out.append(f"{p}: neu, ist={json.dumps(ist[k])[:200]}")
            elif k not in ist:
                out.append(f"{p}: fehlt, referenz={json.dumps(referenz[k])[:200]}")
            else:
                out.extend(befunde(referenz[k], ist[k], p))
        return out
    if isinstance(referenz, list) and isinstance(ist, list):
        out = []
        for i, (r, s) in enumerate(zip(referenz, ist)):
            out.extend(befunde(r, s, f"{pfad}[{i}]"))
        if len(referenz) != len(ist):
            out.append(f"{pfad}: Laenge referenz={len(referenz)} ist={len(ist)}")
        return out
    if referenz != ist:
        return [
            f"{pfad}: referenz={json.dumps(referenz)[:200]} ist={json.dumps(ist)[:200]}"
        ]
    return []


if __name__ == "__main__":
    if "--schreibe" not in sys.argv[1:]:
        sys.exit(
            "Aufruf: python tests/unit/_round_table_charakterisierung.py --schreibe"
        )
    schreibe_referenz(messe_alle())
    print(f"geschrieben: {REFERENZ}")
