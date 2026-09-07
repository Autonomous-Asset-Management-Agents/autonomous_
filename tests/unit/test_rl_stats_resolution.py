# tests/unit/test_rl_stats_resolution.py
# #1875 (MLA-1) — TDD Red-Phase
# Der VecNormalize-Stats-Loader riet den Dateinamen aus dem Version-String
# (rl_stats_{suffix}.pkl) und verfehlte damit das models-v1.0-Bundle
# (rl_agent_v3_dsr_stats.pkl) in JEDER Ship-Installation: die RecurrentPPO-
# Policy (norm_obs=True) bekam Raw-Scale-Beobachtungen — stille Garbage-Votes.
# Fix: Auflösung primär aus data/models_manifest.json, dann Bundle-Konvention
# {version}_stats.pkl, dann Legacy rl_stats_{suffix}.pkl (mit WARNING).

import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import allure
import joblib
import numpy as np
import pytest

import core.strategies.rl_strategy as rl_strategy
from core.strategies.rl_signal import RLSignalMixin
from core.strategies.rl_strategy import RLStrategy, _rl_stats_file

VERSION = "rl_agent_v3_dsr"
BUNDLE_STATS = "rl_agent_v3_dsr_stats.pkl"
LEGACY_STATS = "rl_stats_dsr.pkl"


def _write_manifest(data_dir, stats_entry):
    """Minimal-Manifest im Schema von data/models_manifest.json."""
    manifest = {
        "models": [
            {
                "filename": "scaler_x.pkl",
                "purpose": "StandardScaler for the LSTM input features",
            },
            stats_entry,
        ]
    }
    (Path(data_dir) / "models_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Hermetisches DATA_DIR für den Resolver (kein Repo-/Install-Zustand)."""
    monkeypatch.setattr(rl_strategy, "_data_dir", str(tmp_path))
    monkeypatch.delenv("AAA_MODELS_MANIFEST", raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# 1. _rl_stats_file — Auflösungskette (Manifest → Bundle-Konvention → Legacy)
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestRlStatsFileResolution:
    def test_manifest_filename_wins_over_legacy(self, data_dir, caplog):
        """Bundle-Datei aus dem Manifest schlägt eine parallel existierende Legacy-Datei."""
        _write_manifest(
            data_dir,
            {
                "filename": BUNDLE_STATS,
                "purpose": "VecNormalize observation stats for the v3-DSR RL agent",
            },
        )
        (data_dir / BUNDLE_STATS).write_bytes(b"x")
        (data_dir / LEGACY_STATS).write_bytes(b"x")

        with caplog.at_level(logging.WARNING):
            resolved = _rl_stats_file(VERSION)

        assert resolved == str(data_dir / BUNDLE_STATS)
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_manifest_purpose_match_resolves_custom_filename(self, data_dir):
        """Eintrag zählt auch, wenn nur die purpose auf Stats hinweist (Issue-Scope)."""
        _write_manifest(
            data_dir,
            {
                "filename": "vecnorm_custom.pkl",
                "purpose": "VecNormalize observation stats for the RL agent",
            },
        )
        (data_dir / "vecnorm_custom.pkl").write_bytes(b"x")

        assert _rl_stats_file(VERSION) == str(data_dir / "vecnorm_custom.pkl")

    def test_bundle_convention_fallback_without_manifest(self, data_dir):
        """Ohne Manifest gewinnt {version}_stats.pkl (models-v1.0-Bundle-Konvention)."""
        (data_dir / f"{VERSION}_stats.pkl").write_bytes(b"x")

        assert _rl_stats_file(VERSION) == str(data_dir / f"{VERSION}_stats.pkl")

    def test_legacy_fallback_resolves_and_warns(self, data_dir, caplog):
        """Nur Legacy-Datei vorhanden → wird genutzt, aber WARNING (CODING_POLICY §5.6)."""
        (data_dir / LEGACY_STATS).write_bytes(b"x")

        with caplog.at_level(logging.WARNING):
            resolved = _rl_stats_file(VERSION)

        assert resolved == str(data_dir / LEGACY_STATS)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("legacy" in r.getMessage().lower() for r in warnings)

    def test_version_files_beat_foreign_manifest_entry(self, data_dir, caplog):
        """Ein v5-Agent zieht seine eigenen rl_stats_v5.pkl — nie still den
        versionsfremden (v3-)Manifest-Eintrag (Fresh-Eyes-Review F2 zu #1875)."""
        _write_manifest(
            data_dir,
            {
                "filename": BUNDLE_STATS,
                "purpose": "VecNormalize observation stats for the v3-DSR RL agent",
            },
        )
        (data_dir / BUNDLE_STATS).write_bytes(b"x")  # fremdes v3-Stats-File
        (data_dir / "rl_stats_v5.pkl").write_bytes(b"x")  # eigener v5-Trainer-Output

        with caplog.at_level(logging.WARNING):
            resolved = _rl_stats_file("rl_agent_v5")

        assert resolved == str(data_dir / "rl_stats_v5.pkl")

    @pytest.mark.parametrize(
        "payload",
        ['{"models": null}', "[]", "{not json", '{"models": [42, null]}'],
    )
    def test_malformed_manifest_never_breaks_resolution(self, data_dir, payload):
        """Kaputtes Manifest darf weder Import noch Boot bricken (Review F1)."""
        (data_dir / "models_manifest.json").write_text(payload, encoding="utf-8")
        (data_dir / f"{VERSION}_stats.pkl").write_bytes(b"x")

        assert _rl_stats_file(VERSION) == str(data_dir / f"{VERSION}_stats.pkl")

    def test_nothing_exists_returns_manifest_candidate(self, data_dir):
        """Keine Datei da → primärer (Manifest-)Kandidat zurück, Caller loggt ERROR."""
        _write_manifest(
            data_dir,
            {
                "filename": BUNDLE_STATS,
                "purpose": "VecNormalize observation stats for the v3-DSR RL agent",
            },
        )

        resolved = _rl_stats_file(VERSION)

        assert os.path.basename(resolved) == BUNDLE_STATS
        assert not os.path.exists(resolved)

    def test_resolved_path_matches_shipped_manifest_entry(self, data_dir):
        """Issue-Akzeptanz: aufgelöster Stats-Pfad == Eintrag im echten Repo-Manifest."""
        repo_manifest = (
            Path(rl_strategy.__file__).resolve().parents[2]
            / "data"
            / "models_manifest.json"
        )
        (data_dir / "models_manifest.json").write_text(
            repo_manifest.read_text(encoding="utf-8"), encoding="utf-8"
        )
        entries = json.loads(repo_manifest.read_text(encoding="utf-8"))["models"]
        shipped_stats = next(
            e["filename"]
            for e in entries
            if e["filename"].endswith(".pkl") and "stats" in e["filename"]
        )
        (data_dir / shipped_stats).write_bytes(b"x")

        assert _rl_stats_file(VERSION) == str(data_dir / shipped_stats)


# ---------------------------------------------------------------------------
# 2. RLStrategy._load_vec_normalize — Laden + ERROR-Sichtbarkeit
# ---------------------------------------------------------------------------


def _bare_strategy(rl_model=None):
    strat = RLStrategy.__new__(RLStrategy)
    strat.rl_model = rl_model
    strat.vec_normalize = None
    return strat


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestLoadVecNormalize:
    def test_loads_existing_stats_file(self, tmp_path, monkeypatch):
        """Gherkin: When RLStrategy bootet Then werden die VecNormalize-Stats geladen."""
        # Hermetik: eine gesetzte AAA_MODELS_MANIFEST-Override würde den
        # SHA-Check von safe_joblib_load gegen ein fremdes Manifest laufen lassen.
        monkeypatch.delenv("AAA_MODELS_MANIFEST", raising=False)
        stats_path = tmp_path / BUNDLE_STATS
        joblib.dump(
            SimpleNamespace(obs_rms=SimpleNamespace(mean=np.zeros(3), var=np.ones(3))),
            stats_path,
        )

        import hashlib

        manifest = {
            "models": [
                {
                    "filename": BUNDLE_STATS,
                    "sha256": hashlib.sha256(stats_path.read_bytes()).hexdigest(),
                }
            ]
        }
        (tmp_path / "models_manifest.json").write_text(json.dumps(manifest))

        strat = _bare_strategy(rl_model=object())

        strat._load_vec_normalize(str(stats_path))

        assert strat.vec_normalize is not None

    def test_error_when_policy_loaded_but_stats_missing(self, tmp_path, caplog):
        """Policy geladen + keine Stats = Garbage-Votes-Risiko → ERROR, nie still."""
        strat = _bare_strategy(rl_model=object())

        with caplog.at_level(logging.ERROR):
            strat._load_vec_normalize(str(tmp_path / "missing_stats.pkl"))

        assert strat.vec_normalize is None
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("VecNormalize" in r.getMessage() for r in errors)

    def test_no_error_without_rl_model(self, tmp_path, caplog):
        """LSTM-only-Mode (kein RL-Agent): fehlende Stats sind erwartbar → kein ERROR."""
        strat = _bare_strategy(rl_model=None)

        with caplog.at_level(logging.ERROR):
            strat._load_vec_normalize(str(tmp_path / "missing_stats.pkl"))

        assert not [r for r in caplog.records if r.levelno == logging.ERROR]


# ---------------------------------------------------------------------------
# 3. Parity — Policy-Output mit vs. ohne Stats differiert messbar
# ---------------------------------------------------------------------------


@allure.feature("VC-1 Research & Analysis")
@allure.story("Research & Modeling")
class TestPolicyParityWithVsWithoutStats:
    class _DetPolicy:
        """Deterministischer predict-Stub — reine Funktion der Beobachtung
        (Muster: TestPredictOffload._DetModel in test_rl_execution.py)."""

        def predict(self, obs, state=None, episode_start=None, deterministic=True):
            return np.array([int(abs(float(np.sum(obs)))) % 3]), None

    def test_policy_output_differs_with_vs_without_stats(self):
        """Ohne Stats sieht die Policy Raw-Scale-Obs (Bug #1875); mit Stats
        normalisierte/geclippte Obs — Input UND Policy-Output differieren messbar."""
        mixin = RLSignalMixin.__new__(RLSignalMixin)
        policy = self._DetPolicy()
        raw = np.array([400.0, 150.0, 100.0], dtype=np.float32)

        mixin.vec_normalize = None
        state_without = mixin._normalize_state(raw)
        action_without, _ = policy.predict(
            state_without.reshape(1, -1), deterministic=True
        )

        mixin.vec_normalize = SimpleNamespace(
            obs_rms=SimpleNamespace(mean=np.zeros(3), var=np.ones(3))
        )
        state_with = mixin._normalize_state(raw)
        action_with, _ = policy.predict(state_with.reshape(1, -1), deterministic=True)

        # Beobachtung differiert messbar (clip ±10 statt Raw-Scale) ...
        assert float(np.max(np.abs(state_without - state_with))) > 100.0
        # ... und damit der Policy-Output.
        assert int(action_without[0]) != int(action_with[0])
