# CLAUDE.md — AAA Platform (Autonomous Asset Management Agents) - BORA OSS

> **This file is loaded automatically by Claude Code at every session start.**
> It is the project-specific memory for Claude Code.

---

## 1. Project Overview

This is the **BORA Open Source Edition** of an **AI-driven stock trading platform**.
- **Backend:** Python 3.11+ · FastAPI · LangGraph
- **Infrastructure:** Local-First (Docker Compose)
- **Database:** PostgreSQL (User Data) + Redis (State Management)
- **CLI:** `aaagents` (Plug & Run Experience)

---

## 2. Navigation — Read These First

| Priority | File | Why |
|---|---|---|
| **1st** | `README.md` | Core project entry point and feature overview |
| **2nd** | `docs/oss/ARCHITECTURE.md` | System design, Plugin architecture, Separation of Concerns |
| **3rd** | `docs/oss/TROUBLESHOOTING.md` | Solutions for common Docker and network issues |
| **4th** | `CONTRIBUTING.md` | Guidelines for community contributions |

---

## 3. Critical Rules for Agents

### 3.1 Test-Driven Development (TDD) — Strictly Mandatory
**Red → Green → Refactor. Write the failing test FIRST. No exceptions.**
All tests belong in `tests/unit/` or `tests/regression/`.

### 3.2 Docker-First for Execution & Tests
The entire stack must run locally via Docker Compose.
```bash
docker compose -f docker-compose.oss.yml up --build
```
Never connect directly to the database or Redis without going through the Docker network bindings.

### 3.3 Dependency Injection (DI) & Plugins
BORA uses a strict Plugin Architecture via `core/engine/plugin_registry.py` and `StockSpecialistRegistry`.
- New features (like new stock specialists or data providers) must be implemented as modular plugins.
- Do not modify core orchestration logic (`runner.py`) to hardcode specific features.

### 3.4 LangGraph State Handling
Always use `TypedDict` for LangGraph `State` definitions to ensure smooth execution with Reducers. Only use Pydantic `BaseModel` for strictly validated JSON Structured Outputs from LLMs, and cast them into the dictionary state.

### 3.5 Python Code Quality & Tools
- Use `typer` for CLI interfaces (see `aaagents/`).
- Use `black` and `flake8` for formatting and linting.
- Log graceful fallbacks at `WARNING`, never `DEBUG` or `ERROR` (unless it's a hard crash).

---

## 4. Git & PR Workflow

- **Branching:** Use `feat/<topic>`, `fix/<topic>`, `docs/<topic>`.
- **Commits:** Write clear, conventional commit messages.
- **Pull Requests:** Ensure your code is thoroughly tested via `pytest` before proposing a PR.
- **Pre-Commit Checks:** Run linting (`flake8`) and formatting (`black`) locally before committing.

---

*Maintained by the AAA Open Source Community*
