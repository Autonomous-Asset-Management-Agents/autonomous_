# Quality & Testing Governance

This document describes how the AAAgents codebase is tested, verified, and gated, providing transparency into our software quality processes.

## Dual-Edition Monorepo
The AAAgents platform is developed as a **Dual-Edition Monorepo**. This means the Open-Source (OSS) Edition and the Enterprise Edition live in the same repository.
During the CI process, the `oss_make_snapshot.sh` script (which is published alongside this code) strips out all Enterprise-specific IP (Cloud SQL, multi-tenant layers, etc.) to produce the pure OSS Snapshot.

## Quality Gates
1. **Full-Suite Test Execution:** Our internal `ci.yml` runs the complete test suite (~7000 tests) against the entire codebase, including all OSS features (SQLite, Local Mode, etc.).
2. **OSS Public Testing:** To prove that the OSS snapshot is functional, the public `oss-ci.yml` workflow runs a subset of our deterministic `iron_dome` unit tests natively, ensuring the core math and risk modules execute flawlessly in the OSS-only environment.
3. **Coverage Enforcement:** We enforce a strict coverage minimum (`fail_under = 75`) on our core modules (Risk Manager, Validation, Kill Switch). This is enforced both internally and in the public OSS CI.
4. **Source↔Binary Parity:** To prove that our distributed application runs exactly the open-source code provided here, we execute a cryptographic byte-parity check (`test_3401_snapshot_byte_gleichheit.py`) in our CI pipeline. This ensures that the snapshot you download is verifiable line-by-line.

We believe that true Open-Source Quality requires external verifiability. All critical gates and scripts (including the snapshot generator itself) are open for inspection.
