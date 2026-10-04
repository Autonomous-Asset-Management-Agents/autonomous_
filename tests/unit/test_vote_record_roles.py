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


def test_runner_uses_the_shared_exclusion_source():
    """Keine zweite, driftfähige Ausschlusslogik — der Record leitet Rolle + weight-0
    aus consensus.consensus_exclusions ab (dieselbe Funktion, die der Gate fürs
    Richtungs-Mittel nutzt), nicht aus einer lokalen Namensliste. So kann der
    Audit-/Console-Record nie vom tatsächlichen Gate-Ausschluss abweichen (#3094)."""
    from core.round_table import consensus, runner

    assert runner.consensus_exclusions is consensus.consensus_exclusions


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


# ---------------------------------------------------------------------------
# 4. #3094 SIZER-Rolle — VIXAware verlässt den RICHTUNGS-Record, wenn IV-Forecast an
#    Der Record ist die MiFID-II-Audit-/Console-Quelle: er MUSS den Gate-Ausschluss
#    (consensus_exclusions) widerspiegeln, nicht die statische Default-Rolle. Sonst
#    liest die Console VIXAware als direktionalen Voter mit weight 0.45, obwohl der
#    Gate es namentlich ausschließt (Panel-Divergenz 64 % vs 68 %, live 08.09.).
# ---------------------------------------------------------------------------


def test_serialize_vixaware_is_sizer_when_iv_forecast_on(monkeypatch):
    """IMPLIED_VOL_FORECAST_ENABLED an → VIXAwares IV steuert den Size-Scaler, es
    verlässt das Richtungs-Mittel (consensus_exclusions). Record: weight 0.0 +
    role 'sizer' — KEIN direktionaler Voter. Vote bleibt sichtbar (Score/Reasoning)."""
    import config

    monkeypatch.setattr(
        config.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", True, raising=False
    )
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes(
        [_vote("VIXAwareRiskAgent", 0.12, 0.45), _vote("MomentumAgent", 0.80, 0.45)]
    )

    vix = next(r for r in records if r["agent_name"] == "VIXAwareRiskAgent")
    assert vix["role"] == "sizer", "IV-ausgeschlossener Sizer, nicht 'directional'"
    assert vix["weight"] == 0.0, "nicht im Richtungs-Mittel → kein direktionales Weight"
    assert vix["score"] == 0.12, "Vote bleibt sichtbar"
    mom = next(r for r in records if r["agent_name"] == "MomentumAgent")
    assert mom["role"] == "directional" and mom["weight"] == 0.45


def test_serialize_vixaware_directional_when_iv_forecast_off(monkeypatch):
    """Flag aus → VIXAware ist wieder direktionaler Voter (byte-identisch prä-#3094):
    echtes Weight + role 'directional'."""
    import config

    monkeypatch.setattr(
        config.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", False, raising=False
    )
    from core.round_table.runner import _serialize_votes

    records = _serialize_votes([_vote("VIXAwareRiskAgent", 0.12, 0.45)])

    vix = records[0]
    assert vix["role"] == "directional"
    assert vix["weight"] == 0.45


def test_serialize_guards_stay_excluded_regardless_of_iv_flag(monkeypatch):
    """Die Basis-Conditioner (Regime/DrawdownGuard) bleiben in BEIDEN Flag-Zuständen
    ausgeschlossen (weight 0.0 + ihre Rolle) — consensus_exclusions ⊇ Basis-Set."""
    import config
    from core.round_table.runner import _serialize_votes

    for flag in (True, False):
        monkeypatch.setattr(
            config.get_config(), "IMPLIED_VOL_FORECAST_ENABLED", flag, raising=False
        )
        records = _serialize_votes(
            [
                _vote("RegimeDetectionAgent", 0.76, 0.50),
                _vote("DrawdownGuardAgent", 0.73, 0.60),
            ]
        )
        regime = next(r for r in records if r["agent_name"] == "RegimeDetectionAgent")
        ddg = next(r for r in records if r["agent_name"] == "DrawdownGuardAgent")
        assert regime["weight"] == 0.0 and regime["role"] == "conditioner"
        assert ddg["weight"] == 0.0 and ddg["role"] == "veto_guard"
