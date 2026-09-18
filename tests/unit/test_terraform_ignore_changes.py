"""D1 / Variante C drift guard: the backend Cloud Run service's RUNTIME config is
managed IMPERATIVELY by cloudbuild-backend-deploy.yaml (gen2, max=5, two GCS mounts,
cpu-boost, 10 secrets). resources.tf declares the resource but must ``ignore_changes``
over EVERY cloudbuild-managed attribute, so a ``terraform apply`` reconciles the
declared bits WITHOUT stripping the imperative mounts/secrets.

This guard fails if any of the seven attributes drops out of ignore_changes — the
exact footgun the IaC-reconciliation epic (#1163) is scheduled to retire wholesale.

Parsed with a bounded regex (not a full HCL parser): locate the ``backend`` resource,
clip to its block, pull the first ``ignore_changes = [ ... ]`` — robust to layout.
"""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RESOURCES_TF = _REPO_ROOT / "infra" / "terraform" / "resources.tf"

# Every attribute cloudbuild sets imperatively and terraform must NOT reconcile away.
# Paths verified against the google provider schema for google_cloud_run_v2_service
# (startup_cpu_boost lives INSIDE the containers.resources block, not on containers).
_REQUIRED_IGNORES = [
    "template[0].containers[0].image",
    "template[0].execution_environment",
    "template[0].scaling[0].max_instance_count",
    "template[0].volumes",
    "template[0].containers[0].volume_mounts",
    "template[0].containers[0].resources[0].startup_cpu_boost",
    "template[0].vpc_access",
    # Cloud Build also sets these imperatively; omitting any one lets `terraform apply`
    # strip the live service's secrets/env, IAM identity, timeout or concurrency.
    "template[0].containers[0].env",  # --update-env-vars + --update-secrets
    "template[0].service_account",  # --service-account
    "template[0].timeout",  # --timeout
    "template[0].max_instance_request_concurrency",  # --concurrency
]


def _backend_ignore_changes_block() -> str:
    """Return the ignore_changes list body of the `backend` Cloud Run resource.

    Bracket-depth aware: the attribute paths themselves contain ``[0]`` indices, so a
    naive ``\\[(.*?)\\]`` would stop at the first index. We walk from the opening
    bracket counting depth until it returns to zero — the true closing bracket.
    """
    text = _RESOURCES_TF.read_text(encoding="utf-8")
    start = text.index('resource "google_cloud_run_v2_service" "backend"')
    rest = text[start + 1 :]
    # bound to this resource: stop at the next top-level resource/moved/data block
    nxt = re.search(r"\n(resource |moved |data )", rest)
    block = rest[: nxt.start()] if nxt else rest
    key = block.index("ignore_changes")
    open_br = block.index("[", key)
    depth = 0
    for i in range(open_br, len(block)):
        if block[i] == "[":
            depth += 1
        elif block[i] == "]":
            depth -= 1
            if depth == 0:
                return block[open_br + 1 : i]
    raise AssertionError("unterminated ignore_changes list in backend resource")


def test_resources_tf_exists():
    assert _RESOURCES_TF.is_file(), f"missing {_RESOURCES_TF}"


def test_all_imperative_attributes_are_ignored():
    block = _backend_ignore_changes_block()
    missing = [attr for attr in _REQUIRED_IGNORES if attr not in block]
    assert not missing, f"ignore_changes is missing drift-prone attributes: {missing}"


def test_drift_warning_comment_present():
    # A loud DRIFT-WARNING banner must sit on the backend resource so the next editor
    # knows a bare `terraform apply` is config-only BY DESIGN until epic #1163 lands.
    assert "DRIFT-WARNING" in _RESOURCES_TF.read_text(encoding="utf-8")
