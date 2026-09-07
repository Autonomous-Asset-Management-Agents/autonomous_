# tests/unit/test_vote_record_roles.py
# #3084 — inertes Guard-Weight aus den Vote-Records bereinigen (weight 0.0 + role).
#
# DrawdownGuardAgent/RegimeDetectionAgent sind aus dem direktionalen Konsens-Mittel
# namentlich ausgeschlossen (consensus.py `active_votes`-Filter) — ihr Klassen-Weight
# (0.60/0.50) ist für die Richtung inert. Die Serialisierung (runner.py, Senate-Log →
# votes_json → Console) schrieb es trotzdem in jeden Record und erzeugte belegte
# Fehldeutungen (#1993, AGENT_CATALOG.md:158, Owner-Rückfrage 28.08.).
#
# Fix-Kontrakt (Owner-Direktive 28.08. — Agent bleibt in der Anzeige):
#   1. consensus.NON_DIRECTIONAL_AGENTS — die EINE geteilte Ausschlussliste;
#      Konsens-Filter und Serialisierung lesen dieselbe Konstante.
#   2. runner._serialize_votes(valid_votes) — extrahierte, testbare Naht:
#      nicht-direktionale Agenten → weight 0.0 + role ("veto_guard"/"conditioner"),
#      Vote bleibt ENTHALTEN; direktionale Voter → Weight unverändert + role
#      "directional". Interner VoteResult unberührt (Validator gt=0 + Veto-Pfad
#      laufen weiter auf dem echten Weight).

from __future__ import annotations

from core.round_table.base_agent import VoteResult

EXPECTED_NON_DIRECTIONAL = ("RegimeDetectionAgent", "DrawdownGuardAgent")


def _vote(agent: str, score: float, weight: float, vetoed: bool = False) -> VoteResult:
    return VoteResult(
        agent_name=agent,
        symbol="BNY",
        score=score,
        weight=weight,
        reasoning="test",
        vetoed=vetoed,
    )


# ---------------------------------------------------------------------------
# 1. Geteilte Ausschluss-Konstante
# ---------------------------------------------------------------------------


def test_non_directional_constant_exists_and_matches():
    from core.round_table.consensus import NON_DIRECTIONAL_AGENTS

    assert tuple(sorted(NON_DIRECTIONAL_AGENTS)) == tuple(
        sorted(EXPECTED_NON_DIRECTIONAL)
    )


def test_runner_uses_the_same_constant_object():
    """Keine zweite, driftfähige Namensliste — Runner importiert die Konsens-Liste."""
    from core.round_table import consensus, runner

    assert runner.NON_DIRECTIONAL_AGENTS is consensus.NON_DIRECTIONAL_AGENTS


# ---------------------------------------------------------------------------
# 2. Serialisierungs-Naht: weight 0.0 + role, Agent bleibt sichtbar
# ---------------------------------------------------------------------------


def test_serialize_guard_weight_zeroed_but_vote_kept():
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes(
        [_vote("DrawdownGuardAgent", 0.734, 0.60), _vote("MomentumAgent", 0.954, 0.45)]
    )

    ddg = next(r for r in records if r["agent_name"] == "DrawdownGuardAgent")
    assert ddg["weight"] == 0.0, "inertes Weight darf nicht als 0.60 erscheinen"
    assert ddg["role"] == "veto_guard"
    assert ddg["score"] == 0.734, "Score/Reasoning bleiben — Agent bleibt sichtbar"
    assert ddg["vetoed"] is False


def test_serialize_conditioner_role():
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes([_vote("RegimeDetectionAgent", 0.762, 0.50)])

    regime = records[0]
    assert regime["weight"] == 0.0
    assert regime["role"] == "conditioner"


def test_serialize_directional_weight_unchanged():
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes([_vote("MomentumAgent", 0.954, 0.45)])

    mom = records[0]
    assert mom["weight"] == 0.45
    assert mom["role"] == "directional"
    assert mom["signal"] == "BUY"  # Signal-Ableitung der alten Naht unverändert


def test_serialize_keeps_all_votes_and_signal_mapping():
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes(
        [
            _vote("DrawdownGuardAgent", 0.20, 0.60, vetoed=True),
            _vote("LSTMSignalAgent", 0.30, 0.40),
            _vote("VIXAwareRiskAgent", 0.50, 0.45),
        ]
    )

    assert [r["agent_name"] for r in records] == [
        "DrawdownGuardAgent",
        "LSTMSignalAgent",
        "VIXAwareRiskAgent",
    ]
    ddg, lstm, vix = records
    assert ddg["vetoed"] is True and ddg["signal"] == "SELL"
    assert lstm["signal"] == "SELL"
    assert vix["signal"] == "HOLD"


# ---------------------------------------------------------------------------
# 3. Mechanik-Regression: interner Weight/Veto-Pfad unberührt
# ---------------------------------------------------------------------------


def test_consensus_still_excludes_guards_from_mean():
    """Pin: Konsens = Mittel der direktionalen Stimmen; Guards tragen nicht bei."""
    from core.round_table.consensus import ConsensusEngine

    votes = [
        _vote("DrawdownGuardAgent", 0.10, 0.60),  # würde den Schnitt massiv drücken
        _vote("MomentumAgent", 0.90, 0.45),
        _vote("LSTMSignalAgent", 0.80, 0.40),
    ]
    score = ConsensusEngine().aggregate(votes)
    expected = (0.90 * 0.45 + 0.80 * 0.40) / (0.45 + 0.40)
    assert abs(score - expected) < 1e-9


def test_internal_vote_weight_stays_validator_compatible():
    """Der LIVE VoteResult behält weight > 0 (Validator gt=0; Veto-Pfad braucht
    den gültigen Vote) — nur der RECORD zeigt 0.0."""
    from core.round_table.runner import _serialize_votes

    vote = _vote("DrawdownGuardAgent", 0.5, 0.60)
    _serialize_votes([vote])
    assert vote.weight == 0.60, "Serialisierung darf den Live-Vote nicht mutieren"
