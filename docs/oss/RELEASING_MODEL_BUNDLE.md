# Releasing the OSS Model Bundle

> **Audience:** AAAgents maintainers cutting a new public model bundle.
> **Tournament strategy:** A3 (CI-atomic) — single workflow run does the
> whole thing in one transaction. If any step fails, NOTHING ships.

---

## What this pipeline produces

A freshly-cloned `autonomous_` repo, after `docker compose --env-file .env.oss -f docker-compose.oss.yml up -d`, must end up with the canonical LSTM and RL model files in `./data/` so `LSTMSignalAgent` (w=0.40) and `RLConfidenceAgent` (w=0.40) produce informed Senate votes instead of the neutral-0.5 fallback.

The download chain is:

```
docker compose up
   └─> backend container CMD
         └─> ai_trading_bot/scripts/gcs_sync_on_start.py
               └─> reads data/models_manifest.json
                     └─> for each entry: GET <github-release-url>
                           ├─> SHA256 verify against manifest
                           └─> atomic-write to ./data/<filename>
```

`models_manifest.json` is the single source of truth for both the release
asset URLs *and* the SHA256 integrity hashes. If the manifest is stale, the
download fails closed (SHA mismatch → file is NOT written, engine boots in
degraded mode and logs a WARN — never crashes).

---

## Operator quick start

### Step 0 — One-time prerequisites

- `OSS_DEPLOY_TOKEN` secret on Dev-Enviroment with `contents:write` on
  `Autonomous-Asset-Management-Agents/autonomous_`. (Same secret already
  used by `publish-oss-snapshot.yml` — no new secret needed.)
- A populated `ai_trading_bot/data/` directory containing the canonical model
  files (see "Where models come from" below).

### Step 1 — Stage the model files

The CI-atomic workflow needs the canonical files mounted at
`ai_trading_bot/data/<file>` on the runner before it computes SHA256s.
Three supported flows, **only one** is needed:

| Flow | When to use | How |
|---|---|---|
| **A. Artifact upload** | A prior training workflow (`train_lstm.py`, `train_rl.py`) ran in CI and uploaded the outputs as a GitHub Actions artifact. | Pass `models_artifact: <artifact-name>` when dispatching the workflow. The workflow runs `actions/download-artifact@v4` and unpacks into `ai_trading_bot/data/`. |
| **B. Self-hosted runner** | The training pipeline writes to a persistent volume mounted on a self-hosted runner. | Re-route the workflow to `runs-on: self-hosted` (one-line edit) and ensure the volume is mounted at `ai_trading_bot/data/`. |
| **C. Local checkout (current default)** | Operator runs the workflow from a checkout where they have already populated `ai_trading_bot/data/` from `aaagents-app` locally. | Untrack-edit `ai_trading_bot/data/` files into the runner volume via `gh run download` from a previous "stage models" workflow, OR (for tournament mode) use `act` to run the workflow against a local checkout. |

> **Production migration note:** Flow A is the long-term target — the bundle
> should always derive from a CI training run with full provenance. Flow C
> is documented here because the v1.0 launch ships from operator-curated
> snapshots in `aaagents-app/ai_trading_bot/data/` while the training CI is
> still being built out (out of scope for this tournament).
>
> **Legacy filenames (Flow C):** the v1.0 launch source is the operator's
> `aaagents-app/ai_trading_bot/data/` directory, which contains LEGACY-named
> files (`lstm_model.pth`, `scaler_x.pkl`, `scaler_y.pkl`,
> `model_metadata.json`, `rl_agent_v3_dsr.zip`, `rl_agent_v3_dsr_stats.pkl`).
> The verify-files gate accepts EITHER the canonical v2/v5 set OR this
> legacy set — stage exactly one complete set (mixing partial sets is
> rejected). Flow A and Flow B are expected to ship the canonical v2/v5
> names from CI-trained outputs.

### Step 2 — Choose a release tag

Tags must be unique and sortable. The bundle uses semver-ish tags:

- `models-v1.0` — initial public bundle.
- `models-v1.0.1` — same architecture, fresher training cutoff or bug fix.
- `models-v1.1` — backwards-compatible feature add (e.g. extra senator).
- `models-v2.0` — breaking architecture change (input dim, sequence length).

The workflow **refuses to overwrite** an existing tag. To republish, bump
the patch number — never delete and recreate, because previously-distributed
manifests will still resolve to the old SHA.

### Step 3 — Dispatch the workflow

```bash
gh workflow run publish-oss-model-bundle.yml \
  --ref main \
  -f release_tag=models-v1.0.1
```

> **Token model:** the workflow now uses `OSS_DEPLOY_TOKEN` only on the
> autonomous_ API steps (release create/view/upload + asset re-verify). The
> manifest-PR step uses the auto-injected `GITHUB_TOKEN` because
> `OSS_DEPLOY_TOKEN` does not have PR-write on Dev-Enviroment. There is no
> longer a `trigger_snapshot` input — snapshot regeneration is always a
> manual, post-merge step (see Step 5 below).

What happens, in order (transactional — any failure aborts the rest):

1. **Preflight** — `OSS_DEPLOY_TOKEN` present? required model files staged?
   The verify-files gate accepts EITHER the canonical v2/v5 set OR the
   legacy set documented in Flow C. Mixing partial sets fails loud.
2. **Idempotency** — `gh release view models-v1.0.1` must return non-zero
   (i.e., tag doesn't yet exist on the OSS repo).
3. **Hash** — `build_models_manifest.py --models-dir … --output … --dry-run`
   computes SHA256+size_bytes for every file, writes the manifest JSON.
4. **Release notes** — generated from the manifest (no by-hand markdown).
5. **`gh release create --prerelease --notes-file … <tag> <asset>…`** —
   creates the tag and uploads every asset in a single API call. If the
   release already exists from a partial earlier run: the workflow aborts
   and the operator must `gh release delete --yes <tag>` before retrying.
6. **End-to-end SHA verification** — `build_models_manifest.py --verify` re-
   downloads every asset URL and confirms the manifest SHA matches. Catches
   silent CDN truncation that step 5 returned 0 for.
7. **PR open** — branch `chore/model-bundle-<tag>` with the manifest commit,
   PR raised against Dev-Enviroment `main` for Antigravity review. The
   workflow finishes with a log line reminding the operator that snapshot
   regeneration must be run manually AFTER the PR merges (the snapshot
   reads the manifest from `main`).

### Step 4 — Antigravity review + merge

The PR is small (one JSON file). Reviewer checklist:

- `release_tag` field matches the workflow input.
- Each entry's `url` resolves on `autonomous_` Releases (click through).
- `sha256` lengths are 64 hex chars; no `null`/`""` values.
- `size_bytes` is non-zero and within the 64 MiB hard cap (`gcs_sync_on_start.py`).

Squash-merge as `chore(oss): publish model bundle <tag>`.

### Step 5 — Snapshot regeneration (manual, post-merge)

Snapshot is **always** a manual step run AFTER the manifest PR is merged to
`main`. The previous design had an optional `trigger_snapshot` input that
auto-dispatched here, but `publish-oss-snapshot.yml` reads
`ai_trading_bot/data/models_manifest.json` from `main`, and at workflow-end
time the manifest only lives on the chore branch — the auto-trigger would
ship the OLD manifest. The input has been removed; run snapshot manually:

```bash
# After Antigravity squash-merges the chore/model-bundle-<tag> PR:
gh workflow run publish-oss-snapshot.yml --ref main
```

`scripts/oss_make_snapshot.sh` step 2.4 copies `data/models_manifest.json`
into the OSS snapshot. The manifest then ships into `autonomous_` on the
next force-push and is consumed by every cloned-from-public engine on first
container boot.

---

## Where the models come from

The current canonical source is `C:\Users\gapel\aaagents-app\ai_trading_bot\data\` (operator's local box):

| File (canonical / v2 alias) | Layer | License |
|---|---|---|
| `lstm_model.pth` ≈ `lstm_model_v2.pth` | LSTM 5-day return predictor | CC-BY-4.0 |
| `scaler_x.pkl` ≈ `scaler_x_v2.pkl` | LSTM input feature scaler | CC-BY-4.0 |
| `scaler_y.pkl` ≈ `scaler_y_v2.pkl` | LSTM target scaler | CC-BY-4.0 |
| `model_metadata.json` ≈ `model_metadata_v2.json` | feature list + hyperparams | CC-BY-4.0 |
| `rl_agent_v3_dsr.zip` ≈ `rl_agent_v5.zip` | RecurrentPPO RL agent | CC-BY-4.0 |
| `rl_agent_v3_dsr_stats.pkl` ≈ `rl_stats_v5.pkl` | VecNormalize stats | CC-BY-4.0 |

`build_models_manifest.py` accepts both naming generations and emits flat
filenames; the canonical (v2/v5) names are the bundle's stable contract.

> **Booster heads + senator ensembles** (`data/models/<file>`): not yet
> shipped in the OSS bundle — see "Path mapping" below.

---

## Path mapping: flat filenames vs `data/models/` subdir

`gcs_sync_on_start.py` enforces flat filenames in the manifest
(`_is_safe_filename` rejects `/` and `\`) so every file lands directly under
`./data/<name>`. Production matches that layout — `gs://aaa-trading-bot-models/data/`
is also flat.

The local-only `aaagents-app/ai_trading_bot/data/models/` subdirectory
contains ~491 TFT directories and ~2146 .pt models which the engine *currently
reads from `data/models/`*. They are **out of scope** for the v1.0 bundle.
Reasoning:

- The 6 top-level files unblock the headline failure mode (LSTM + RL voting
  neutral 0.5). The booster/senator layer adds breadth but not "trade or no
  trade" determinism.
- Shipping the 90-file subdir requires either (i) a post-download mover
  script invoked after `gcs_sync_on_start.py`, or (ii) a schema bump that
  adds an optional `dest_subdir` field to manifest entries.
- Either path touches files adjacent to the live trading boot sequence —
  out of scope for the trading-zero-impact tournament budget. Tracked as a
  follow-up on the OSS roadmap. When unblocked, option (i) is preferred:
  add a separate `scripts/setup_oss_models_subdirs.py` that consumes a
  second manifest (`models_manifest_subdirs.json`) and moves files into
  `data/models/`. `gcs_sync_on_start.py` stays untouched.

---

## Recovery from partial failure

| Symptom | Recovery |
|---|---|
| Step 5 created the release but step 6 (verify) failed | `gh release delete <tag> --yes --repo Autonomous-Asset-Management-Agents/autonomous_` then re-run. |
| Step 7 (PR open) failed but release exists | The release is already public. Manually create the manifest commit on a new branch and `gh pr create` — the manifest lives at `ai_trading_bot/data/models_manifest.json` in the workflow's runner artefacts under `models_manifest.json` (re-download via `gh run download <run-id>`). |
| Snapshot ran but downloaded files SHA-mismatch on user box | The release assets have drifted vs the manifest. Bump the tag, re-run the workflow — never edit a published manifest in place. |

---

## Scripts referenced

- `ai_trading_bot/scripts/build_models_manifest.py` — manifest builder
  + verifier. New CLI flags `--models-dir` (alias `--from-dir`), `--org`,
  `--dry-run` are all documented in `python build_models_manifest.py --help`.
- `ai_trading_bot/scripts/gcs_sync_on_start.py` — engine-startup downloader.
  No changes for the A3 strategy: the manifest schema has been the same since
  the bundle was first scoped (filename / url / sha256 / size_bytes).
- `scripts/oss_make_snapshot.sh` — adds step 2.4 to copy the manifest into
  the public snapshot. The bundle never ships into git history; only the
  manifest JSON does.
- `.github/workflows/publish-oss-model-bundle.yml` — the CI-atomic publisher.

---

## Trading-path zero-impact statement

The A3 pipeline is purely an asset-bootstrapping mechanism. Every change
lands in:

- Workflow YAML (CI-only).
- Build/release scripts (operator-only).
- Snapshot script step (operator-only).
- Documentation (this file).

The engine code (`core/engine/`, `core/strategies/`, `core/round_table/`,
`core/agents/`) is not modified. `gcs_sync_on_start.py` is unchanged — the
manifest schema it consumes is the same one it has consumed since v0.

See `SELF_REVIEW.md` (worktree root) for the grep evidence.
