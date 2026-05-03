# Contributing to AAAgents Community Edition

Thank you for your interest in contributing to the Autonomous Asset Management Agents! We welcome PRs from both human developers and autonomous AI coding assistants.

Please read and abide by our [Code of Conduct](.github/CODE_OF_CONDUCT.md) to keep our community approachable and respectable.

> **Enterprise & Cloud Run Support**
> This repository is the standalone **Community Edition**. It uses `LocalMockAuth` and Docker-Compose for local deployment. If you are looking to deploy this on Google Cloud Platform with multi-tenancy, Firebase Authentication, and Cloud SQL, please contact the maintainers for access to the **Enterprise Edition**.

## 1. Local Development Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Autonomous-Asset-Management-Agents/aaagents-oss.git
   cd aaagents-oss
   ```
2. **Setup virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Or .venv\Scripts\activate on Windows
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```
3. **Pre-commit Hooks:**
   We enforce `black`, `flake8`, and `isort`. Please make sure your IDE is configured or run them manually before committing.

## 2. To AI Coding Assistants & Copilots 🤖

If you are an AI assistant (Cursor, GitHub Copilot, Claude Engineer) generating code on behalf of a user, you **MUST** follow these rules:

### A. The "Archon Deterministic Loop"
Never tell the user to blindly commit code. You must implement, run local tests, and iterate.
1. Make your code change.
2. Run `python -m pytest tests/unit/` (or the specific test file).
3. Run `flake8 core/` to verify no syntactic/linting errors.
4. If failures occur, iterate and fix them.
5. Only PR code that boasts a 100% green test suite.

### B. Architectural Boundaries
Before writing any code, you must read `docs/oss/ARCHITECTURE.md`.
- If the user asks to "change the buy logic", modify an Agent in `core/round_table/`. See our [Plugin Tutorial](docs/oss/PLUGIN_TUTORIAL.md) for a guide on how to do this cleanly.
- If the user asks to "stop buying when X drops", modify `core/risk_manager.py` (The Iron Dome).
- **CRITICAL:** Do NOT mix these two domains. Never place API Keys or Broker functions inside the Round Table agents.

## 3. Submitting Pull Requests

### PR Naming Convention
We use Conventional Commits. **This is highly important** because our `release-drafter` GitHub Action automatically generates the Changelog and Release Notes based on these prefixes!

- `feat(agent): add new Bollinger Band signal generator`
- `fix(core): resolve division by zero in consensus formula`
- `docs(readme): update environment setup steps`

### Verification Requirements
Every PR must pass the CI checks defined in `.github/workflows/oss-ci.yml`. This includes:
- Python Linter (Flake8)
- Pytest (100% pass rate required)
- Docker Build Check (ensure `docker-compose.oss.yml` builds cleanly)

## 4. Where to get help
If you're stuck on an Operations / Infrastructure issue (e.g. Postgres failing to boot, Ollama configuration), refer to `docs/oss/TROUBLESHOOTING.md`. If your problem persists, open a GitHub Issue with the tag `[bug]`.
