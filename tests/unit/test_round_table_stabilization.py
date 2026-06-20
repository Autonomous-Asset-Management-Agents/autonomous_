# tests/unit/test_round_table_stabilization.py
# TDD Red-Phase: Round Table Stabilization (5 Fixes)
#
# Abgedeckte Fixes:
#   Fix 1: runner.py — Per-Agent VOTE[SYM] Logging
#   Fix 2: rl_strategy.py — JSON-Guard + weights_only Fallback + Startup-Log
#   Fix 3: rl_signal.py — is_v3 erkennt v5 (12-dim Observation)
#   Fix 5: engine/base.py — SpecialistAlpha Warm-up Diagnose
#
# Policy: CODING_POLICY.md §11.5 TDD, §1 Compliance-First (MiFID II)

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, call, patch

import allure
import numpy as np
import pytest

from core.round_table.runner import boot_engine


@pytest.fixture(autouse=True)
def setup_di():
    boot_engine(None)


# ===========================================================================
# Fix 1: runner.py — Per-Agent VOTE[SYM] Logging (MiFID II Audit-Fallback)
# ===========================================================================


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRunnerVoteLogging:
    """
    Gherkin:
      Given: A Round Table cycle completes with valid votes
      When:  run_round_table() aggregates results
      Then:  A VOTE[SYM] log line per agent is emitted at INFO level
             (format: VOTE[{symbol}] agent={name} score={:.3f} weight={:.2f} reasoning={...})
    """

    def _make_vote(
        self, name: str, score: float = 0.6, weight: float = 0.5, symbol: str = "AAPL"
    ) -> Any:
        from core.round_table.base_agent import VoteResult

        return VoteResult(
            agent_name=name,
            symbol=symbol,
            score=score,
            weight=weight,
            reasoning=f"{name} test reasoning",
            vetoed=False,
        )

    def _make_state(self, symbol: str = "AAPL") -> Dict:
        return {
            "symbol": symbol,
            "ohlc": {"close": 150.0, "volume": 1_000_000},
            "error": None,
        }

    @pytest.mark.anyio
    async def test_vote_log_emitted_per_agent(self):
        """VOTE[SYM] log erscheint für jeden erfolgreichen Agent-Vote."""
        from core.round_table.runner import run_round_table

        state = self._make_state("AAPL")
        votes = [
            self._make_vote("DrawdownGuardAgent", 0.7, 0.6),
            self._make_vote("TrendFollowAgent", 0.6, 0.5),
            self._make_vote("LSTMSignalAgent", 0.55, 0.4),
        ]

        # Agent-Mocks VOR dem patch-Kontext erstellen
        mock_agent_list = []
        for v in votes:
            a = MagicMock()
            a.vote = AsyncMock(return_value=v)
            mock_agent_list.append(a)

        # Direct logger mock — deterministisch, kein caplog race condition
        info_calls: list[str] = []

        def capture_info(fmt, *args, **kwargs):
            info_calls.append(fmt % args if args else fmt)

        with patch(
            "core.round_table.runner._active_agents", new=mock_agent_list
        ), patch("core.round_table.runner._consensus_engine") as mock_ce, patch(
            "core.round_table.runner._gatekeeper"
        ) as mock_gk, patch(
            "core.round_table.runner._senate"
        ) as mock_senate, patch(
            "core.round_table.runner.logger"
        ) as mock_logger:

            mock_logger.info.side_effect = capture_info
            mock_ce.aggregate.return_value = 0.63
            mock_ce.check_distribution = MagicMock(return_value=(True, "ok"))
            gk_dec = MagicMock(approved=True, reason="AllChecksPassed")
            mock_gk.check = AsyncMock(return_value=gk_dec)
            mock_senate.log_session = AsyncMock(return_value=None)

            await run_round_table(state)

        vote_logs = [msg for msg in info_calls if msg.startswith("VOTE[AAPL]")]
        assert len(vote_logs) == 3, (
            f"Erwartet 3 VOTE[AAPL] Logs, erhalten: {len(vote_logs)}\n"
            f"Alle geloggten INFO Nachrichten: {info_calls}"
        )

    @pytest.mark.anyio
    async def test_vote_log_contains_agent_name_score_weight(self, caplog):
        """VOTE-Log enthält agent_name, score (3 Dezimalstellen) und weight."""
        from core.round_table.runner import run_round_table

        state = self._make_state("VLO")
        vote = self._make_vote("DrawdownGuardAgent", score=0.823, weight=0.6)

        # Agent-Mock VOR dem patch-Kontext erstellen
        a = MagicMock()
        a.vote = AsyncMock(return_value=vote)

        with patch("core.round_table.runner._active_agents", new=[a]), patch(
            "core.round_table.runner._consensus_engine"
        ) as mock_ce, patch("core.round_table.runner._gatekeeper") as mock_gk, patch(
            "core.round_table.runner._senate"
        ) as mock_senate:

            mock_ce.aggregate.return_value = 0.82
            mock_ce.check_distribution = MagicMock(return_value=(True, "ok"))
            gk_dec = MagicMock(approved=True, reason="OK")
            mock_gk.check = AsyncMock(return_value=gk_dec)
            mock_senate.log_session = AsyncMock()

            with caplog.at_level(logging.INFO, logger="core.round_table.runner"):
                await run_round_table(state)

        vote_log = next((r for r in caplog.records if "VOTE[VLO]" in r.message), None)
        assert vote_log is not None, "VOTE[VLO] log nicht gefunden"
        assert "DrawdownGuardAgent" in vote_log.message
        assert "0.823" in vote_log.message
        assert "0.60" in vote_log.message or "0.6" in vote_log.message


# ===========================================================================
# Fix 2: rl_strategy.py — JSON-Guard + weights_only Fallback + Startup-Log
# ===========================================================================


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRLStrategyLoadTorchModelAssets:
    """
    Gherkin (JSON-Guard):
      Given: model_metadata_v2.json ohne 'features_list' key
      When:  _load_torch_model_assets() aufgerufen
      Then:  torch_model bleibt None, ERROR wird geloggt (kein KeyError-Crash)

    Gherkin (weights_only Fallback):
      Given: torch.load mit weights_only=True wirft UnpicklingError
      When:  _load_torch_model_assets() aufgerufen
      Then:  Retry mit weights_only=False, torch_model geladen

    Gherkin (Startup-Log):
      Given: _load_torch_model_assets() scheitert vollständig
      When:  RLStrategy.__init__() nach _load_torch_model_assets()
      Then:  logging.error("❌ CRITICAL: torch_model=None") wird geloggt
    """

    def _make_minimal_strategy(self, tmp_dir: str):
        """
        Erstellt eine RLStrategy-ähnliche Klasse mit nur den relevanten Attributen,
        ohne den vollen __init__ (der RL-Modelle und externe APIs benötigt).
        """
        import torch

        from core.strategies.rl_strategy import RLStrategy

        # Wir patchen schwere Dependencies
        with patch(
            "core.strategies.rl_strategy.os.path.exists", return_value=False
        ), patch(
            "core.strategies.rl_strategy.get_trade_intelligence", return_value=None
        ):
            strategy = object.__new__(RLStrategy)
            strategy.device = torch.device("cpu")
            strategy.torch_model = None
            strategy.scaler_x = None
            strategy.scaler_y = None
            strategy.features_list = []
            strategy.torch_metadata = {}
        return strategy

    def test_json_guard_missing_features_list(self, tmp_path, caplog):
        """
        Gherkin:
          Given: metadata JSON ohne 'features_list'
          When:  _load_torch_model_assets()
          Then:  torch_model=None, ❌ ERROR geloggt, kein KeyError
        """
        import torch

        # Erstelle minimale Dateien
        metadata = {
            "model_params": {
                "input_dim": 10,
                "hidden_dim": 64,
                "num_layers": 2,
                "output_dim": 1,
            }
        }
        meta_path = tmp_path / "model_metadata_v2.json"
        meta_path.write_text(json.dumps(metadata))

        model_path = tmp_path / "lstm_model_v2.pth"
        model_path.touch()  # Leere Datei reicht für diesen Test

        scaler_x_path = tmp_path / "scaler_x_v2.pkl"
        scaler_x_path.touch()
        scaler_y_path = tmp_path / "scaler_y_v2.pkl"
        scaler_y_path.touch()

        from core.strategies.rl_strategy import RLStrategy

        strategy = object.__new__(RLStrategy)
        strategy.device = __import__("torch").device("cpu")
        strategy.torch_model = None
        strategy.scaler_x = None
        strategy.scaler_y = None
        strategy.features_list = []

        paths = (
            str(model_path),
            str(scaler_x_path),
            str(scaler_y_path),
            str(meta_path),
        )
        with patch(
            "core.strategies.rl_strategy.get_lstm_paths", return_value=paths
        ), caplog.at_level(logging.ERROR):
            strategy._load_torch_model_assets()

        assert (
            strategy.torch_model is None
        ), "torch_model muss None bleiben bei fehlendem features_list"
        error_logs = [
            r
            for r in caplog.records
            if "features_list" in r.message or "model_params" in r.message
        ]
        assert (
            error_logs
        ), f"❌ ERROR-Log erwartet, gefunden: {[r.message for r in caplog.records]}"

    def test_weights_only_true_fails_securely(self, tmp_path, caplog):
        """
        Gherkin:
          Given: weights_only=True wirft Exception (Inkompatibilität)
          When:  _load_torch_model_assets()
          Then:  Exception wird geworfen und fail-closed erzwungen (kein Fallback auf False)
        """
        import torch

        from models.torch_model import LSTMModel

        # Erstelle valide Metadaten
        input_dim = 5
        metadata = {
            "features_list": [f"feat_{i}" for i in range(input_dim)],
            "model_params": {
                "input_dim": input_dim,
                "hidden_dim": 32,
                "num_layers": 1,
                "output_dim": 1,
            },
        }
        meta_path = tmp_path / "model_metadata_v2.json"
        meta_path.write_text(json.dumps(metadata))

        # Erstelle valides LSTM-Modell
        model = LSTMModel(input_dim, 32, 1, 1)
        model_path = tmp_path / "lstm_model_v2.pth"
        torch.save(model.state_dict(), str(model_path))

        # Erstelle valide Scaler
        import joblib
        import numpy as np
        from sklearn.preprocessing import StandardScaler

        scaler_x = StandardScaler()
        scaler_x.fit(np.random.rand(10, input_dim))
        scaler_y = StandardScaler()
        scaler_y.fit(np.random.rand(10, 1))

        scaler_x_path = tmp_path / "scaler_x_v2.pkl"
        scaler_y_path = tmp_path / "scaler_y_v2.pkl"
        joblib.dump(scaler_x, str(scaler_x_path))
        joblib.dump(scaler_y, str(scaler_y_path))

        from core.strategies.rl_strategy import RLStrategy

        strategy = object.__new__(RLStrategy)
        strategy.device = torch.device("cpu")
        strategy.torch_model = None
        strategy.scaler_x = None
        strategy.scaler_y = None
        strategy.features_list = []

        paths = (
            str(model_path),
            str(scaler_x_path),
            str(scaler_y_path),
            str(meta_path),
        )

        original_torch_load = torch.load
        call_count = [0]

        def mock_torch_load(path, map_location=None, weights_only=True):
            call_count[0] += 1
            if weights_only is True:
                raise RuntimeError("Simulated weights_only=True incompatibility")
            return original_torch_load(
                path, map_location=map_location, weights_only=False
            )

        with patch(
            "core.strategies.rl_strategy.get_lstm_paths", return_value=paths
        ), patch("torch.load", side_effect=mock_torch_load), caplog.at_level(
            logging.ERROR
        ):
            strategy._load_torch_model_assets()

        assert (
            call_count[0] == 1
        ), "torch.load darf nur 1x mit weights_only=True gerufen werden"

        error_logs = [
            r
            for r in caplog.records
            if "FAILED to load PyTorch model: RuntimeError: Simulated weights_only=True incompatibility"
            in r.message
        ]
        assert (
            error_logs
        ), f"❌ ERROR-Log erwartet, gefunden: {[r.message for r in caplog.records]}"

    def test_successful_load_logs_success(self, tmp_path, caplog):
        """
        Gherkin:
          Given: Alle Model-Dateien valide
          When:  _load_torch_model_assets() erfolgreich
          Then:  ✅ torch_model geladen geloggt, torch_model nicht None
        """
        import joblib
        import numpy as np
        import torch
        from sklearn.preprocessing import StandardScaler

        from models.torch_model import LSTMModel

        input_dim = 5
        metadata = {
            "features_list": [f"feat_{i}" for i in range(input_dim)],
            "model_params": {
                "input_dim": input_dim,
                "hidden_dim": 32,
                "num_layers": 1,
                "output_dim": 1,
            },
        }
        meta_path = tmp_path / "model_metadata_v2.json"
        meta_path.write_text(json.dumps(metadata))

        model = LSTMModel(input_dim, 32, 1, 1)
        model_path = tmp_path / "lstm_model_v2.pth"
        torch.save(model.state_dict(), str(model_path))

        scaler_x = StandardScaler()
        scaler_x.fit(np.random.rand(10, input_dim))
        scaler_y = StandardScaler()
        scaler_y.fit(np.random.rand(10, 1))
        scaler_x_path = tmp_path / "scaler_x_v2.pkl"
        scaler_y_path = tmp_path / "scaler_y_v2.pkl"
        joblib.dump(scaler_x, str(scaler_x_path))
        joblib.dump(scaler_y, str(scaler_y_path))

        from core.strategies.rl_strategy import RLStrategy

        strategy = object.__new__(RLStrategy)
        strategy.device = torch.device("cpu")
        strategy.torch_model = None
        strategy.scaler_x = None
        strategy.scaler_y = None
        strategy.features_list = []

        paths = (
            str(model_path),
            str(scaler_x_path),
            str(scaler_y_path),
            str(meta_path),
        )
        with patch(
            "core.strategies.rl_strategy.get_lstm_paths", return_value=paths
        ), caplog.at_level(logging.INFO):
            strategy._load_torch_model_assets()

        assert (
            strategy.torch_model is not None
        ), "torch_model muss nach erfolgreichem Load gesetzt sein"
        success_logs = [
            r
            for r in caplog.records
            if "✅" in r.message and "model" in r.message.lower()
        ]
        assert (
            success_logs
        ), f"✅ Erfolgs-Log erwartet. Vorhandene Logs: {[r.message for r in caplog.records]}"


# ===========================================================================
# Fix 3: rl_signal.py — is_v3 erkennt v5 → 12-dim Observation Space
# ===========================================================================


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestRLSignalV5ObservationSpace:
    """
    Gherkin:
      Given: _rl_model_version = "rl_agent_v5"
      When:  _get_current_state() baut den Observation-Vektor
      Then:  Vektor hat 12 Dimensionen (inkl. volatility_regime)
             — NICHT 11 (was falsches RL-Verhalten verursacht)

    RED: Aktuell liefert is_v3 = "v3" in "rl_agent_v5" = False → 11-dim (FALSCH)
    GREEN: Nach Fix is_v3 = any(x in version for x in ("v3","v4","v5")) → True → 12-dim
    """

    def _make_mixin_v5(self):
        import torch

        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin._rl_model_version = "rl_agent_v5"
        mixin.vec_normalize = None
        mixin.client = MagicMock()
        mixin.client.get_open_position = MagicMock(return_value=None)
        mixin.symbols = ["AAPL"]
        mixin.torch_model = None
        mixin.scaler_x = None
        mixin.data_provider = MagicMock()
        mixin._current_vix = 22.0
        mixin._vix_regime = "normal"
        return mixin

    def _make_valid_features(self):
        """Minimal Feature-Series für _get_current_state."""
        import pandas as pd

        return pd.DataFrame(
            [
                {
                    "close": 150.0,
                    "returns": 0.01,
                    "rsi_14": 50.0,
                    "macd": 0.2,
                    "macd_signal": 0.1,
                    "bb_pct": 0.5,
                    "volume": 1_000_000,
                    "volume_sma_20d": 900_000,
                    "volatility_20d": 0.020,
                    "momentum_10d": 0.01,
                    "adx_14": 25.0,
                }
            ]
        )

    @pytest.mark.anyio
    async def test_v5_builds_12_dim_observation(self):
        """
        RED: "v3" in "rl_agent_v5" → False → 11-dim (BUG)
        GREEN: any(["v3","v4","v5"] in version) → True → 12-dim (FIX)
        """
        mixin = self._make_mixin_v5()
        features_df = self._make_valid_features()

        # Mock _get_torch_prediction to return (pred, features)
        with patch.object(
            mixin,
            "_get_torch_prediction",
            new_callable=AsyncMock,
            return_value=(0.5, features_df),
        ):
            raw_state, features, pred = await mixin._get_current_state(
                "AAPL", datetime.now(timezone.utc), {"vix": 22.0}
            )

        assert raw_state is not None, "_get_current_state darf nicht None zurückgeben"
        assert len(raw_state) == 12, (
            f"v5 muss 12-dim Observation bauen (hat volatility_regime), "
            f"got {len(raw_state)}. Bug: 'v3' in 'rl_agent_v5' = False → 11-dim."
        )

    @pytest.mark.anyio
    async def test_v3_still_builds_12_dim_observation(self):
        """v3 muss weiterhin 12-dim liefern (Regression-Test)."""
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin._rl_model_version = "rl_agent_v3_dsr"
        mixin.vec_normalize = None
        mixin.client = MagicMock()
        mixin.client.get_open_position = MagicMock(return_value=None)
        mixin.symbols = ["AAPL"]
        mixin.torch_model = None
        mixin.scaler_x = None
        mixin.data_provider = MagicMock()
        mixin._current_vix = 22.0

        features_df = self._make_valid_features()
        with patch.object(
            mixin,
            "_get_torch_prediction",
            new_callable=AsyncMock,
            return_value=(0.5, features_df),
        ):
            raw_state, _, _ = await mixin._get_current_state(
                "AAPL", datetime.now(timezone.utc), {"vix": 22.0}
            )

        assert raw_state is not None
        assert (
            len(raw_state) == 12
        ), f"v3 muss 12-dim Observation haben, got {len(raw_state)}"

    @pytest.mark.anyio
    async def test_v4_builds_12_dim_observation(self):
        """v4 muss ebenfalls 12-dim liefern (neu abgedeckt)."""
        from core.strategies.rl_signal import RLSignalMixin

        mixin = RLSignalMixin.__new__(RLSignalMixin)
        mixin._rl_model_version = "rl_agent_v4"
        mixin.vec_normalize = None
        mixin.client = MagicMock()
        mixin.client.get_open_position = MagicMock(return_value=None)
        mixin.symbols = ["AAPL"]
        mixin.torch_model = None
        mixin.scaler_x = None
        mixin.data_provider = MagicMock()
        mixin._current_vix = 22.0

        features_df = self._make_valid_features()
        with patch.object(
            mixin,
            "_get_torch_prediction",
            new_callable=AsyncMock,
            return_value=(0.5, features_df),
        ):
            raw_state, _, _ = await mixin._get_current_state(
                "AAPL", datetime.now(timezone.utc), {"vix": 22.0}
            )

        assert raw_state is not None
        assert (
            len(raw_state) == 12
        ), f"v4 muss 12-dim Observation haben, got {len(raw_state)}"


# ===========================================================================
# Fix 5: engine/base.py — SpecialistAlpha Warm-up Diagnose
# ===========================================================================


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestSpecialistWarmupCheck:
    """
    Gherkin:
      Given: StockSpecialistRegistry initialisiert und gestartet
      When:  BotEngine.__init__() aufgerufen
      Then:  Ein Background-Thread "SpecialistWarmup" wurde gestartet

    Gherkin (warm-up vollständig):
      Given: Warmup-Thread läuft
      When:  60s vergangen, registry._reports hat 0 Einträge
      Then:  logging.warning("0 reports") wurde geloggt
    """

    def test_warmup_check_scheduled_after_registry_start(self):
        """
        Gibt es nach set_specialist_registry() einen SpecialistWarmup-Thread?
        Getestet durch direkten Aufruf von _schedule_specialist_warmup_check().
        """
        from core.engine.base import BotEngine

        engine = object.__new__(BotEngine)
        engine.specialist_registry = MagicMock()
        engine.specialist_registry._reports = {}  # 0 reports

        active_before = {t.name for t in threading.enumerate()}

        engine._schedule_specialist_warmup_check()

        # Thread muss gestartet worden sein
        active_after = {t.name for t in threading.enumerate()}
        assert (
            "SpecialistWarmup" in active_after
        ), f"'SpecialistWarmup' Thread nicht gefunden. Aktive Threads: {active_after}"

    def test_warmup_check_logs_zero_reports_as_warning(self, caplog):
        """
        Wenn nach 60s 0 Reports gecacht → WARNING geloggt.
        Wir testen mit sehr kurzer Sleep-Zeit (patch time.sleep).
        """
        from core.engine.base import BotEngine

        engine = object.__new__(BotEngine)
        engine.specialist_registry = MagicMock()
        engine.specialist_registry._reports = {}  # 0 reports

        with caplog.at_level(logging.WARNING):
            with patch("time.sleep", return_value=None):  # kein 60s warten
                # Direkter Aufruf des Inner-Threads
                engine._schedule_specialist_warmup_check()

                # Warte kurz damit der Thread starten kann
                time.sleep(0.1)
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    warning_logs = [
                        r
                        for r in caplog.records
                        if "0 reports" in r.message or "neutral 0.5" in r.message
                    ]
                    if warning_logs:
                        break
                    time.sleep(0.05)

        assert warning_logs, (
            f"WARNING über 0 reports erwartet. "
            f"Vorhandene Logs: {[r.message for r in caplog.records]}"
        )

    def test_warmup_check_logs_cached_report_count(self, caplog):
        """
        Wenn Reports gecacht → INFO Log mit Anzahl.
        """
        from core.engine.base import BotEngine

        engine = object.__new__(BotEngine)
        engine.specialist_registry = MagicMock()
        engine.specialist_registry._reports = {"AAPL": {}, "VLO": {}, "TSLA": {}}

        with caplog.at_level(logging.INFO):
            with patch("time.sleep", return_value=None):
                engine._schedule_specialist_warmup_check()

                time.sleep(0.1)
                deadline = time.time() + 3.0
                while time.time() < deadline:
                    info_logs = [
                        r
                        for r in caplog.records
                        if "3 reports" in r.message or "warm-up" in r.message.lower()
                    ]
                    if info_logs:
                        break
                    time.sleep(0.05)

        assert info_logs, (
            f"INFO Log mit 3 reports erwartet. "
            f"Vorhandene Logs: {[r.message for r in caplog.records]}"
        )


# ===========================================================================
# Fix P2: SpecialistAlphaAgent + NewsSentimentAgent
#         weight=0.0 wenn kein Signal → aus Konsens ausgeschlossen
# ===========================================================================


@allure.feature("VC-2 Portfolio Construction")
@allure.story("Portfolio & Strategy")
class TestP2AgentWeightZeroExclusion:
    """
    SpecialistAlpha: kein Report → weight=0.0 (nicht 0.55 + score=0.5)
    NewsSentiment:   Gemini fehlt → weight=0.0 (nicht 0.35 + score=0.5)
    ConsensusEngine: weight=0.0 Votes werden durch Pydantic gt=0.0 ausgeschlossen
    BotEngine:       update_priority() wird beim Start aufgerufen
    """

    def _make_state(self, symbol: str = "AAPL") -> Dict:
        return {
            "symbol": symbol,
            "ohlc": {"close": 150.0, "open": 148.0, "high": 151.0, "low": 147.0},
        }

    @pytest.mark.anyio
    async def test_specialist_alpha_no_report_weight_zero(self):
        from core.round_table.agents import SpecialistAlphaAgent

        agent = SpecialistAlphaAgent()
        mock_registry = MagicMock()
        mock_registry.get_report.return_value = None
        with patch(
            "core.round_table.agents._specialist_registry_instance", new=mock_registry
        ):
            result = await agent.vote(self._make_state())
        assert result.weight == 0.0, (
            f"Kein Report -> weight muss 0.0 sein. Got: {result.weight}. "
            f"BUG: weight={result.weight} + score=0.5 zieht Konsens zu 0.5!"
        )

    @pytest.mark.anyio
    async def test_specialist_alpha_no_registry_weight_zero(self):
        from core.round_table.agents import SpecialistAlphaAgent

        agent = SpecialistAlphaAgent()
        with patch("core.round_table.agents._specialist_registry_instance", new=None):
            result = await agent.vote(self._make_state())
        assert (
            result.weight == 0.0
        ), f"Kein Registry -> weight=0.0. Got: {result.weight}"

    @pytest.mark.anyio
    async def test_specialist_alpha_valid_report_full_weight(self):
        from core.round_table.agents import SpecialistAlphaAgent

        agent = SpecialistAlphaAgent()
        mock_report = MagicMock()
        mock_report.sentiment_score = 75.0
        mock_report.recommendation = "buy"
        mock_report.escalate = False
        mock_registry = MagicMock()
        mock_registry.get_report.return_value = mock_report
        with patch(
            "core.round_table.agents._specialist_registry_instance", new=mock_registry
        ):
            result = await agent.vote(self._make_state())
        assert (
            result.weight == agent.weight
        ), f"Mit Report: weight={agent.weight}. Got: {result.weight}"
        assert result.score > 0.5, f"buy+75 -> score > 0.5. Got: {result.score}"

    @pytest.mark.anyio
    async def test_news_sentiment_gemini_unavailable_weight_zero(self):
        from core.round_table.agents import NewsSentimentAgent

        agent = NewsSentimentAgent()
        # LLM provider unavailable (no Gemini key / Ollama down) → the seam
        # returns None → the vote must be excluded (weight=0), not pulled to 0.5.
        with patch("core.round_table.agents.get_llm_provider", return_value=None):
            result = await agent.vote(self._make_state())
        assert result.weight == 0.0, (
            f"LLM unavailable -> weight=0.0. Got: {result.weight}. "
            f"BUG: 0.35 + score=0.5 zieht Konsens zu 0.5!"
        )

    def test_consensus_excludes_weight_zero_votes(self):
        from core.round_table.base_agent import VoteResult
        from core.round_table.consensus import ConsensusEngine

        engine = ConsensusEngine()
        votes = [
            VoteResult(
                agent_name="DrawdownGuardAgent",
                symbol="AAPL",
                score=0.80,
                weight=0.60,
                reasoning="bullish",
            ),
            VoteResult(
                agent_name="SpecialistAlphaAgent",
                symbol="AAPL",
                score=0.50,
                weight=0.0,
                reasoning="EXCLUDED kein Report",
            ),
            VoteResult(
                agent_name="NewsSentimentAgent",
                symbol="AAPL",
                score=0.50,
                weight=0.0,
                reasoning="EXCLUDED Gemini fehlt",
            ),
        ]
        result = engine.aggregate(votes)
        assert (
            abs(result - 0.80) < 0.01
        ), f"weight=0.0 Votes ausgeschlossen -> ~0.80 (nur DrawdownGuard). Got: {result:.4f}"

    def test_engine_calls_update_priority_at_startup(self):
        from core.engine.base import BotEngine

        engine = object.__new__(BotEngine)
        engine.specialist_registry = MagicMock()
        initial_symbols = ["AAPL", "VLO", "TSLA", "NVDA", "MSFT"]
        engine._register_high_priority_symbols_at_startup(initial_symbols)
        engine.specialist_registry.update_priority.assert_called_once_with(
            initial_symbols
        )
