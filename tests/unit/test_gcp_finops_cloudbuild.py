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
    """Terraform deklariert die konservative Cleanup Policy fuer Prod & Staging."""
    tf_text = _RESOURCES_TF.read_text(encoding="utf-8")
    # Terraform richtet Zuweisungen am laengsten Namen im Block aus, die
    # Einrueckung je Wert wechselt also mit dem Nachbarn. Auf rohem Text zu
    # pruefen hiesse, die Formatierung festzunageln statt den Inhalt.
    norm = " ".join(tf_text.split())
    ar_type = "google_artifact_registry_repository"
    assert f'resource "{ar_type}" "trading_bot"' in tf_text
    assert f'resource "{ar_type}" "trading_bot_staging"' in tf_text, (
        "Staging Artifact Registry (aaagents-nonprod-srv-01-x5r9/trading-bot) "
        "fehlt in resources.tf"
    )

    # Beide Repository-Bloecke tragen dieselben vier Regeln.
    for policy_id in (
        "behalte-neueste-engine",
        "loesche-unmarkierte",
        "loesche-trainingsimages",
        "loesche-alte-engine",
    ):
        assert (
            norm.count(f'id = "{policy_id}"') == 2
        ), f'Regel "{policy_id}" fehlt in einem der beiden Repository-Bloecke'

    assert "keep_count = 30" in norm  # Rollback-Fenster
    assert 'older_than = "604800s"' in norm  # unmarkiert + Trainingsimages: 7 d
    assert 'older_than = "5184000s"' in norm  # Engine-Images: 60 d


def test_terraform_cleanup_keeps_rollback_window() -> None:
    """Die 3-Tage-Loeschung darf nicht zurueckkommen.

    Befund 02.11.2026: Die urspruengliche Fassung loeschte `tag_state = ANY`
    aelter als 3 Tage bei `keep_count = 3`. Eine Cloud-Run-Revision laedt ihr
    Image bei JEDEM Kaltstart erneut — ein Rollback auf eine Revision
    ausserhalb der letzten drei haette also kein Image mehr gefunden. Dass es
    nie aufgefallen ist, liegt daran, dass die Policy nie angewendet wurde:
    das Repository trug am 02.11. kein `cleanupPolicies`-Feld und war auf
    1,73 TB gewachsen.
    """
    norm = " ".join(_RESOURCES_TF.read_text(encoding="utf-8").split())
    assert 'older_than = "259200s"' not in norm, (
        "3-Tage-DELETE ist zurueck in resources.tf — es macht ein Rollback auf "
        "eine aeltere Cloud-Run-Revision unmoeglich."
    )
    assert (
        "keep_count = 3 " not in f"{norm} "
    ), "keep_count = 3 ist zurueck — zu wenig fuer ein Rollback-Fenster."


def test_cleanup_script_includes_staging_pkg_dev_repo() -> None:
    """Cleanup-Skript bereinigt auch das Nonprod Artifact Registry Repo."""
    script_text = _CLEANUP_SH.read_text(encoding="utf-8")
    host = "europe-west3-docker.pkg.dev"
    repo = "${STAGING_PROJECT_ID}/trading-bot"
    assert "aaagents-nonprod-srv-01-x5r9" in script_text
    assert f"{host}/{repo}/trading-bot-engine" in script_text
    assert f"{host}/{repo}/trading-bot-train" in script_text
