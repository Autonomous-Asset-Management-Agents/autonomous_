# ==============================================================
# AAAgents OSS — Makefile
#
# OSS Quick-Start:
#   make setup    # First-time setup: generate .env.oss with secure secrets
#   make start    # Start the full stack (runs setup if needed)
#   make stop     # Stop all containers
#   make logs     # Tail backend logs
#   make reset    # Nuclear reset: remove all containers + volumes
#
# Local CI (requires act + Docker):
#   make ci-local   # Run the full OSS CI suite (test-oss-stack) locally via act
# ==============================================================

.PHONY: setup start stop logs reset ci-local ci-check help pre-flight

# ── OSS User Targets ──────────────────────────────────────────────────────────

# Detect OS for cross-platform setup script selection
ifeq ($(OS),Windows_NT)
    SETUP_CMD := powershell -ExecutionPolicy Bypass -File setup.ps1
else
    SETUP_CMD := bash setup.sh
endif

COMPOSE_FILE := docker-compose.oss.yml
ENV_FILE     := .env.oss

# Bundle the env-file + compose-file flags into a single COMPOSE handle. The
# --env-file flag is required because Docker Compose only auto-loads `.env`
# (not `.env.oss`); without it every ${VAR:?…} guard in the compose file
# would fail interpolation even though setup.sh has populated .env.oss.
COMPOSE := docker compose --env-file $(ENV_FILE) -f $(COMPOSE_FILE)

# NOTE: On Windows, run make from Git Bash (not cmd.exe/PowerShell).
# Git Bash is already required for setup.sh, so this is not an additional constraint.

setup: ## First-time setup: generate .env.oss with cryptographically secure secrets
	@$(SETUP_CMD)

start: ## Start the AAAgents OSS stack (runs setup first if .env.oss is missing)
	@test -f .env.oss || $(MAKE) setup
	$(COMPOSE) up -d
	@echo ""
	@echo "Stack started. Dashboard: http://localhost"
	@echo "Follow logs: make logs"

stop: ## Stop all containers (data is preserved)
	$(COMPOSE) down

logs: ## Tail backend engine logs (Ctrl+C to exit)
	$(COMPOSE) logs -f backend

reset: ## Nuclear reset: stop containers AND delete all volumes (paper-trading history lost)
	@echo "⚠  This will delete all local paper-trading data. Continue? [y/N]" && read ans && [ $${ans:-N} = y ]
	$(COMPOSE) down -v
	@echo "✅ Reset complete. Run 'make start' to restart."

# ── Local CI Targets ──────────────────────────────────────────────────────────

# Default runner image — matches ARC Ubuntu runner environment
ACT_RUNNER := catthehacker/ubuntu:act-latest

# Secrets file for local runs — copy from .secrets.ci.example
SECRETS_FILE := .secrets.ci

pre-flight: ## Run Agentic Pre-Flight PR review simulation
	python scripts/agentic_pre_flight.py

help: ## Show available make targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ci-check: ## Verify act is installed and Docker is running
	@command -v act >/dev/null 2>&1 || (echo "ERROR: act not installed. Run: winget install nektos.act" && exit 1)
	@docker info >/dev/null 2>&1 || (echo "ERROR: Docker is not running" && exit 1)
	@echo "✅ act and Docker are ready"

ci-local: ci-check ## Run the full OSS CI suite locally via act (mirrors GitHub Actions test-oss-stack job)
	act push \
		--job test-oss-stack \
		--platform ubuntu-latest=$(ACT_RUNNER) \
		--secret-file $(SECRETS_FILE) \
		--no-cache-server \
		--rm
