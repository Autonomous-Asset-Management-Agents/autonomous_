# Plan: OSS Model Bundle — Default LSTM/RL Models for Self-Host Launch

## Status: TODO

## Goal

Ship `aaagents-oss` with proven default LSTM + RL models so `LSTMSignalAgent` (w=0.40) and `RLConfidenceAgent` (w=0.40) — currently neutral 0.5 fallback because `data/` is empty and `scripts/gcs_sync_on_start.py` is missing — produce meaningful votes out-of-the-box.

**Why now:** OSS launch is the public face of the platform. Today's repo state has three integration bugs that mean a fresh `git clone && docker compose up` produces a "lobotomised" Senate where 2 of 9 voting agents (~20% of consensus weight) fall back to neutral. Worse: `tests/unit/test_gcs_sync_on_start.py` (170 lines) was copied from Dev-Enviroment but the production script `scripts/gcs_sync_on_start.py` it imports was not — so CI is broken on a clean checkout.

**Performance evidence:** Same LSTM architecture (34-feature, hidden_dim=128, num_layers=3) + RecurrentPPO RL agent v5 ran on a paper-trading account from 2026-01-14 → 2026-04-30 (3.5 months, 359+ trades, 61 unique symbols, Alpaca Paper), reaching $113.9k from $100k seed (+13.9%). SPY in same window: +3.58% (yfinance close-to-close). This is out-of-sample relative to the LSTM training cutoff (Jan 10, 2026) and RL training cutoff (Feb 9, 2026). **n=1, single regime, not a Sharpe-graded backtest** — see README disclaimer.

## 1. Three integration bugs this PR fixes

| Bug | Today | Fix |
|---|---|---|
| `scripts/gcs_sync_on_start.py` missing | Test imports it → CI fails on every commit | Create the script |
| `scripts/setup_oss_models.sh` is a `[MOCK]`-only stub (all `curl` lines commented out) | User runs it → "MOCK" echos but no actual download → docker compose runs with empty `data/` | Replace with a working delegate to the python sync |
| `docker-compose.oss.yml` skips sync **and** mounts the wrong subdir | Container boots with empty `data/` → ML agents neutral 0.5 | Prepend `python scripts/gcs_sync_on_start.py &&` to the backend `command:`; change volume `./data/models:/app/app/data/models` → `./data:/app/app/data` (full data dir, what the engine actually reads from per `core/strategies/rl_strategy.py:36-37`) |

## 2. Change Impact Analysis (CIA)

### Radius
| File | Change Type |
|---|---|
| `scripts/gcs_sync_on_start.py` | **new** (170+ lines) — sync-source dispatcher with GCS path stub + GitHub-Release primary path |
| `scripts/build_models_manifest.py` | **new** (137 lines) — operator helper: `--from-dir` builds, `--verify` rechecks live release |
| `scripts/setup_oss_models.sh` | rewritten — replaces `[MOCK]` echos with delegate to `gcs_sync_on_start.py` |
| `data/models_manifest.json` | **new** — 6 entries, real SHA256 + size_bytes, URLs to `aaagents-oss/releases/download/models-v1.0/` |
| `tests/unit/test_gcs_sync_on_start.py` | **fixed** — now passes (script exists). Extended with 14 new tests (6 GitHub-Release happy-path + 8 security-guard / robustness) |
| `docker-compose.oss.yml` | command override now invokes sync; volume mount fixed to `./data:/app/app/data` |
| `README.md` | new "Default ML Models" section + past-performance disclaimer block |
| `docs/oss/plans/oss-model-bundle-plan.md` | **new** — this doc |

### Severity
**LOW.**

- **No capital loss risk:** model loading is the same as today; only the bootstrap that puts files in `data/` changes. RLStrategy / Round Table / Risk Manager / Kill Switch / Compliance Gatekeeper code is **byte-identical** to before this PR.
- **No new runtime dependencies:** stdlib only (`urllib.request`, `hashlib`, `json`, `os`).
- **Non-blocking on every failure mode:** sync returns 0 even if every download fails. Engine boots in degraded mode (current state), never crashes.
- **Security guards on the new code path:** URL allow-list (only `https://github.com/` + `https://objects.githubusercontent.com/`), filename traversal guard (rejects `/`, `\`, `..`, leading `.`), size cap (`size_bytes + 1 MiB` slack, hard ceiling 64 MiB), atomic write via `.part + os.replace`. Defends against a malicious manifest entry like `file:///etc/passwd` or a hostile mirror returning gigabytes.

### Rollback
< 2 minutes:
1. Revert PR.
2. Optional: delete the `models-v1.0` release tag if already created.

## 3. Gherkin Acceptance Criteria

```gherkin
Feature: aaagents-oss default models

  Scenario: Fresh clone + docker compose up
    Given the user has just cloned aaagents-oss
    And data/models_manifest.json ships in the repo
    When they run "docker compose -f docker-compose.oss.yml up -d"
    Then the backend container runs gcs_sync_on_start.py first
    And manifest is read, 6 files downloaded from GitHub Release with SHA256 verified
    And files land in /app/app/data/ (mounted from host ./data/)
    And alembic migrates, then engine starts
    And LSTMSignalAgent + RLConfidenceAgent vote with informed scores in the Senate

  Scenario: Pre-populate via setup script
    Given the user runs "bash scripts/setup_oss_models.sh"
    Then the same sync logic writes the same 6 files to ./data/
    And docker compose up sees them already present (sync would skip on re-verify)

  Scenario: No internet during boot
    Given the user has no internet
    When the container starts
    Then sync logs WARN per file, exits 0
    And the engine starts in degraded mode (LSTM + RL agents neutral 0.5)

  Scenario: Malicious manifest tampered downstream
    Given a manifest entry has filename "../../etc/cron.d/evil" or url "file:///etc/passwd"
    When sync runs
    Then the entry is refused with a WARN
    And no file is written outside DATA_DIR
    And no non-https URL is opened
```

## 4. Tasks (TDD-ordered)

- [x] Branch `feat/oss-model-bundle` from `origin/main`
- [x] Port hardened `gcs_sync_on_start.py` from Dev-Enviroment branch (security guards already in)
- [x] Create `data/models_manifest.json` with real SHA256 + URLs to `aaagents-oss` release
- [x] Create `scripts/build_models_manifest.py` (default repo = aaagents-oss)
- [x] Rewrite `scripts/setup_oss_models.sh` (delegate to python sync, point at root `data/`)
- [x] Fix `docker-compose.oss.yml` (command + volume mount)
- [x] Extend `tests/unit/test_gcs_sync_on_start.py` (+14 tests for GitHub-Release path and security guards)
- [x] README new "Default ML Models" section with disclaimer
- [x] Run pytest — 22/22 green
- [x] Run black — clean
- [x] Smoke test: real-world boot (release does not yet exist) → 6× HTTP 404 → graceful WARN → exit 0
- [ ] Commit + push
- [ ] PR open + hand-off

## 5. Pre-merge operator step (manual)

```bash
gh release create models-v1.0 \
  --repo Autonomous-Asset-Management-Agents/aaagents-oss \
  --notes "Community Baseline Models v1.0 — see docs/oss/plans/oss-model-bundle-plan.md"

# upload the 6 assets (rename lstm_model.pth → lstm_model_v2.pth on upload):
gh release upload models-v1.0 \
  --repo Autonomous-Asset-Management-Agents/aaagents-oss \
  /path/to/lstm_model_v2.pth \
  /path/to/scaler_x_v2.pkl \
  /path/to/scaler_y_v2.pkl \
  /path/to/model_metadata_v2.json \
  /path/to/rl_agent_v5.zip \
  /path/to/rl_stats_v5.pkl

# Verify hashes match the manifest in this PR:
python scripts/build_models_manifest.py --verify data/models_manifest.json
```

## 6. Decisions

- **Pull source = GitHub Release Asset.** Not Git LFS (quota), not image-baked (rebuild cycle), not repo-committed (binary bloat).
- **Manifest in `data/models_manifest.json`** next to the consumer; JSON not YAML (no PyYAML dep).
- **Zero new runtime deps.** stdlib only.
- **Operator owns release creation.** Helper automates SHA computation but `gh release create` is a manual deploy step (avoids putting binary blobs in CI).
- **Bind-mount `./data` not just `./data/models`** — the engine reads the parent dir, not the (never-used) `models/` subfolder.

## 7. Blockers / open questions

- None. Snapshot artefacts available at `D:/aitb_kopie/AI Trading Bot - Kopie/AI Trading Bot/data/`. Snapshot has `lstm_model.pth` (no `_v2` suffix) but file is metadata-compatible; rename on upload to `lstm_model_v2.pth`.

## 8. Next action

Commit + push + open PR.
