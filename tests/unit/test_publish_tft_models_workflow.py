"""Structural tests for .github/workflows/publish-tft-models.yml (Issue 3b).

The workflow's real run is operator-only (a ~1.3 GB upload on workflow_dispatch),
so it cannot be executed in CI. These tests pin the governance + structural
invariants the reviewer relies on: it is dispatch-only, hits autonomous_ with
the right tokens, builds the bundle+manifest via #1131's scripts, re-verifies the
published tar, and touches NO PR-CI / deploy / trading-path file.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_WORKFLOW = (
    Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "publish-tft-models.yml"
)


def _load() -> dict:
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def test_workflow_exists_and_parses() -> None:
    assert _WORKFLOW.is_file(), f"missing workflow at {_WORKFLOW}"
    doc = _load()
    assert doc.get("name") == "Publish TFT Serving Bundle"


def test_dispatch_only_no_push_or_pr_triggers() -> None:
    doc = _load()
    # PyYAML parses the bare key `on:` as the boolean True.
    triggers = doc.get("on", doc.get(True))
    assert isinstance(triggers, dict)
    assert set(triggers.keys()) == {
        "workflow_dispatch"
    }, "publish-tft-models must be operator-dispatch only — never push/PR/schedule"
    assert "release_tag" in triggers["workflow_dispatch"]["inputs"]


def test_targets_public_oss_repo() -> None:
    env = _load()["jobs"]["publish-tft-bundle"]["env"]
    assert env["OSS_ORG"] == "Autonomous-Asset-Management-Agents"
    assert env["OSS_REPO"] == "autonomous_"


def test_uses_1131_build_scripts() -> None:
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert "build_tft_serving_bundle.py" in body
    assert "build_tft_manifest.py" in body


def test_idempotency_and_post_upload_verify_present() -> None:
    steps = _load()["jobs"]["publish-tft-bundle"]["steps"]
    names = [s.get("name", "") for s in steps]
    assert any("Idempotency" in n for n in names), "must refuse an existing tag"
    assert any(
        "Verify published bundle" in n for n in names
    ), "must re-verify the upload"
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert "--verify" in body and "--prerelease" in body


def test_manifest_embedded_in_bundle_tar() -> None:
    """B3: the manifest must be copied into the tree ROOT and the tar re-built AFTER,
    so 3a's whole-tar extraction lands it at <TFT_MODELS_ROOT>/tft_models_manifest.json —
    exactly where the verify-gate (#1142) and boot-verify (#1144) read it."""
    body = _WORKFLOW.read_text(encoding="utf-8")
    assert (
        'cp "${MANIFEST_OUT}" "${STAGING}/tft_models_manifest.json"' in body
    ), "manifest must be embedded at the tree root before tarring (B3)"
    assert (
        'tar -czf "${BUNDLE_TAR}" -C "${STAGING}" .' in body
    ), "bundle must be (re-)tarred AFTER the manifest is embedded (B3)"


def test_two_token_separation() -> None:
    """OSS-repo steps use OSS_DEPLOY_TOKEN; the Dev-Enviroment manifest PR uses
    the auto-injected GITHUB_TOKEN (OSS_DEPLOY_TOKEN lacks PR-write here)."""
    steps = _load()["jobs"]["publish-tft-bundle"]["steps"]
    by_name = {s.get("name", ""): s for s in steps}
    create = by_name["Create release + upload the bundle tar (atomic)"]
    assert "OSS_DEPLOY_TOKEN" in str(create["env"]["GH_TOKEN"])
    pr_step = by_name["Commit manifest on chore branch + open PR"]
    assert "GITHUB_TOKEN" in str(pr_step["env"]["GH_TOKEN"])


def test_trading_path_zero_impact() -> None:
    """The workflow must not read or modify any engine/strategy/round_table file
    — it is an operator-facing publish pipeline only."""
    body = _WORKFLOW.read_text(encoding="utf-8")
    for forbidden in ("core/engine/", "core/strategies/", "core/round_table/"):
        # allowed to MENTION them in the zero-impact comment, but never as a path
        # the steps `run`. Crude but effective: no step references them in a run.
        assert f"python {forbidden}" not in body
        assert f"cd {forbidden}" not in body


def test_attestation_permissions_present() -> None:
    """F: SLSA build-provenance needs OIDC (id-token) + attestations write, in
    addition to the contents/PR scopes the publish steps already use."""
    perms = _load()["permissions"]
    assert perms.get("id-token") == "write", "OIDC token required for Sigstore signing"
    assert (
        perms.get("attestations") == "write"
    ), "must be allowed to store the attestation"
    # the pre-existing scopes must survive the addition
    assert perms.get("contents") == "write"
    assert perms.get("pull-requests") == "write"


def test_attestation_step_signs_the_bundle_tar() -> None:
    """F: an actions/attest-build-provenance step must sign exactly the published
    bundle tar (which embeds the manifest at its root)."""
    steps = _load()["jobs"]["publish-tft-bundle"]["steps"]
    attest = [
        s
        for s in steps
        if str(s.get("uses", "")).startswith("actions/attest-build-provenance")
    ]
    assert len(attest) == 1, "exactly one build-provenance attestation step expected"
    assert attest[0]["with"]["subject-path"] == "${{ env.BUNDLE_TAR }}"


def test_attestation_runs_after_publish_verify() -> None:
    """F: attest a PROVEN-GOOD artifact — the attestation step must come after the
    end-to-end re-hash verify, never before it."""
    steps = _load()["jobs"]["publish-tft-bundle"]["steps"]
    names = [s.get("name", "") for s in steps]
    verify_idx = next(i for i, n in enumerate(names) if "Verify published bundle" in n)
    attest_idx = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/attest-build-provenance")
    )
    assert attest_idx > verify_idx, "attestation must follow the publish-verify step"
