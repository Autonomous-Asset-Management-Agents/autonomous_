"""FinOps Guard: Staging Cloud Build darf kein 8-GB-GPU-Trainings-Image bauen,
und Artifact Registry Cleanup Policies muessen sowohl Prod als auch Staging
(aaagents-nonprod-srv-01-x5r9/trading-bot) abdecken.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_STAGING_CLOUDBUILD = _REPO_ROOT / "ai_trading_bot" / "cloudbuild.yaml"
_RESOURCES_TF = _REPO_ROOT / "infra" / "terraform" / "resources.tf"
_CLEANUP_SH = _REPO_ROOT / "scripts" / "artifact_registry_cleanup.sh"


def _active_lines(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


def test_staging_cloudbuild_skips_train_image() -> None:
    """Staging-Deploy baut nur trading-bot-engine, nicht Dockerfile.train."""
    active = "\n".join(_active_lines(_STAGING_CLOUDBUILD))
    assert "Dockerfile.train" not in active, (
        "ai_trading_bot/cloudbuild.yaml baut noch Dockerfile.train — "
        "verursacht ~5-8 GB CUDA-Image pro Staging-Deploy ohne Verwendung."
    )
    assert "trading-bot-train" not in active, (
        "ai_trading_bot/cloudbuild.yaml referenziert noch trading-bot-train — "
        "Staging deployed ausschliesslich trading-bot-engine."
    )
    assert "trading-bot-engine" in active


def test_terraform_artifact_registry_cleanup_policies() -> None:
    """Terraform deklariert 3d/3-Rev Cleanup Policy fuer Prod & Staging."""
    tf_text = _RESOURCES_TF.read_text(encoding="utf-8")
    ar_type = "google_artifact_registry_repository"
    assert f'resource "{ar_type}" "trading_bot"' in tf_text
    assert f'resource "{ar_type}" "trading_bot_staging"' in tf_text, (
        "Staging Artifact Registry (aaagents-nonprod-srv-01-x5r9/trading-bot) "
        "fehlt in resources.tf"
    )
    assert 'older_than = "259200s"' in tf_text
    assert "keep_count = 3" in tf_text


def test_cleanup_script_includes_staging_pkg_dev_repo() -> None:
    """Cleanup-Skript bereinigt auch das Nonprod Artifact Registry Repo."""
    script_text = _CLEANUP_SH.read_text(encoding="utf-8")
    host = "europe-west3-docker.pkg.dev"
    repo = "${STAGING_PROJECT_ID}/trading-bot"
    assert "aaagents-nonprod-srv-01-x5r9" in script_text
    assert f"{host}/{repo}/trading-bot-engine" in script_text
    assert f"{host}/{repo}/trading-bot-train" in script_text
