"""Signal-Replay — aufgezeichnete Stimmen statt neu gerechneter Agenten.

Der Replay friert die Signalschicht ein: LSTM, Momentum, Specialist usw. liefern
ihre historischen Werte, alles darueber (Konsens, Veto, Sizing, Portfolio) laeuft
mit der aktuellen Implementierung. Damit ist der A/B-Vergleich frei vom
Agenten-Nichtdeterminismus.
"""

import pytest

from core.round_table.agents import LSTMSignalAgent, MomentumAgent, VIXAwareRiskAgent
from research.signal_replay import (
    ReplayVoteStore,
    build_replay_agents,
    install_replay_agents,
    set_replay_cycle,
)

TS = "2026-08-11T13:45:00+00:00"


@pytest.fixture
def store():
    return ReplayVoteStore(
        {
            ("AAPL", TS): {
                "MomentumAgent": (0.864, 0.45),
                "LSTMSignalAgent": (0.243, 0.40),
                "VIXAwareRiskAgent": (0.512, 0.45),
            },
            ("MSFT", TS): {
                "MomentumAgent": (0.301, 0.45),
                # LSTM fehlt an diesem Zyklus -> muss zur Enthaltung fuehren
            },
        }
    )


class _State(dict):
    """Minimaler SymbolEvalState-Ersatz — die Agenten lesen nur `symbol`."""


@pytest.mark.asyncio
async def test_replay_agent_returns_recorded_score_and_weight(store):
    agents = {a.__class__.__name__: a for a in build_replay_agents(store)}
    set_replay_cycle(TS)
    vote = await agents["MomentumAgent"].vote(_State(symbol="AAPL"))
    assert vote.score == pytest.approx(0.864)
    assert vote.weight == pytest.approx(0.45)
    assert vote.agent_name == "MomentumAgent"
    assert vote.symbol == "AAPL"


@pytest.mark.asyncio
async def test_missing_record_abstains_with_zero_weight(store):
    agents = {a.__class__.__name__: a for a in build_replay_agents(store)}
    set_replay_cycle(TS)
    vote = await agents["LSTMSignalAgent"].vote(_State(symbol="MSFT"))
    assert vote.weight == 0.0, "ohne Aufzeichnung darf der Agent nicht mitstimmen"


@pytest.mark.asyncio
async def test_unknown_symbol_abstains(store):
    agents = {a.__class__.__name__: a for a in build_replay_agents(store)}
    set_replay_cycle(TS)
    vote = await agents["MomentumAgent"].vote(_State(symbol="NVDA"))
    assert vote.weight == 0.0


def test_replay_agents_keep_real_class_identity(store):
    """SEC: runner.py:668/673 prueft isinstance(), NICHT __class__.__name__.

    Ein Stub, der den Namen nur vorgibt, wuerde den ML-Pfad still umgehen
    (Anti-Spoofing-Sperre). Die Stubs muessen daher von den echten Klassen erben.
    """
    agents = {a.__class__.__name__: a for a in build_replay_agents(store)}
    assert isinstance(agents["LSTMSignalAgent"], LSTMSignalAgent)
    assert isinstance(agents["MomentumAgent"], MomentumAgent)
    assert isinstance(agents["VIXAwareRiskAgent"], VIXAwareRiskAgent)
    # und der Name bleibt exakt erhalten (runner.py:654 _agent_type_map)
    assert agents["LSTMSignalAgent"].__class__.__name__ == "LSTMSignalAgent"


def test_install_replaces_active_agents_and_guards_purity(store):
    """V2-Gate: nach dem Einsetzen darf KEIN echter Agent mehr im Gremium sein."""
    from core.round_table import runner as rt_runner

    original = rt_runner._active_agents
    try:
        agents = build_replay_agents(store)
        install_replay_agents(agents)
        assert rt_runner._active_agents is agents
        with pytest.raises(RuntimeError, match="kein Replay-Agent"):
            install_replay_agents(list(agents) + [MomentumAgent()])
    finally:
        rt_runner._active_agents = original


def test_replay_survives_a_later_boot_engine(store):
    """Archon-Befund: boot_engine() setzt _active_agents neu.

    run_sim() bootet die Engine selbst — ein einmaliges Setzen vor dem Boot waere
    wirkungslos. activate_replay() huellt boot_engine, damit das Gremium auch
    nach einem spaeteren (Re-)Boot aus Replay-Agenten besteht.
    """
    from core.round_table import runner as rt_runner
    from research.signal_replay import activate_replay, deactivate_replay

    original_boot = rt_runner.boot_engine
    original_agents = rt_runner._active_agents
    calls = []
    try:
        rt_runner.boot_engine = lambda *a, **kw: calls.append(  # echter Boot ersetzt
            rt_runner.__dict__.__setitem__("_active_agents", [MomentumAgent()])
        )
        activate_replay(store)
        assert all(
            getattr(a, "_is_replay_agent", False) for a in rt_runner._active_agents
        )

        rt_runner.boot_engine()  # ein spaeterer Boot holt die echten Agenten zurueck …
        assert calls, "der umhuellte Boot muss den echten aufrufen"
        # … und die Huelle setzt das Replay-Gremium unmittelbar wieder ein
        assert all(
            getattr(a, "_is_replay_agent", False) for a in rt_runner._active_agents
        )
    finally:
        deactivate_replay()
        rt_runner.boot_engine = original_boot
        rt_runner._active_agents = original_agents


def test_store_lookup_is_as_of_not_exact(store):
    """As-of: die zuletzt aufgezeichnete Stimme mit Zeitstempel <= jetzt.

    Kausal korrekt — genau das steht einem Live-System in diesem Moment zur
    Verfuegung. Der exakte Abgleich waere zu streng: die Engine faehrt eine
    andere Kadenz als die Aufzeichnung und traefe die Zeitstempel nie.
    """
    assert store.get("AAPL", TS, "MomentumAgent") == (0.864, 0.45)
    # eine Minute spaeter: dieselbe Stimme steht weiterhin
    assert store.get("AAPL", "2026-08-11T13:46:00+00:00", "MomentumAgent") == (
        0.864,
        0.45,
    )


def test_store_never_returns_a_future_vote(store):
    """Eine SPAETERE Stimme darf nie zurueckkommen — das waere Vorauswissen."""
    assert store.get("AAPL", "2026-08-11T13:44:59+00:00", "MomentumAgent") is None


def test_store_takes_the_most_recent_of_several():
    later = "2026-08-11T14:00:00+00:00"
    s = ReplayVoteStore(
        {
            ("AAPL", TS): {"MomentumAgent": (0.10, 0.45)},
            ("AAPL", later): {"MomentumAgent": (0.90, 0.45)},
        }
    )
    assert s.get("AAPL", "2026-08-11T13:59:59+00:00", "MomentumAgent") == (0.10, 0.45)
    assert s.get("AAPL", later, "MomentumAgent") == (0.90, 0.45)
    assert s.get("AAPL", "2026-08-11T23:00:00+00:00", "MomentumAgent") == (0.90, 0.45)
