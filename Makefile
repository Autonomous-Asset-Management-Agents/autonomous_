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
#   make ci-local          # Run full backend smoke test locally
#   make ci-lint           # Run lint gate only
#   make ci-backend-smoke  # Run backend smoke test
#   make ci-round-table    # Run Round Table V2 gate
# ==============================================================

.PHONY: setup start stop logs reset ci-local ci-lint ci-backend-smoke ci-round-table ci-check help

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
	@python -c "import sys,os; sys.exit(0) if os.path.exists('.env.oss') else sys.exit(1)" || $(MAKE) setup
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

help: ## Show available make targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

ci-check: ## Verify act is installed and Docker is running
	@command -v act >/dev/null 2>&1 || (echo "ERROR: act not installed. Run: winget install nektos.act" && exit 1)
	@docker info >/dev/null 2>&1 || (echo "ERROR: Docker is not running" && exit 1)
	@echo "✅ act and Docker are ready"

ci-local: ci-check ## Run backend smoke test locally (fastest feedback loop)
	act push \
		--job backend-smoke-test \
		--platform ubuntu-latest=$(ACT_RUNNER) \
		--secret-file $(SECRETS_FILE) \
		--no-cache-server \
		--rm

ci-lint: ci-check ## Run lint gate only (no secrets needed)
	act push \
		--job lint \
		--platform ubuntu-latest=$(ACT_RUNNER) \
		--no-cache-server \
		--rm

ci-backend-smoke: ci-check ## Run backend smoke test (requires .secrets.ci)
	act push \
		--job backend-smoke-test \
		--platform ubuntu-latest=$(ACT_RUNNER) \
		--secret-file $(SECRETS_FILE) \
		--no-cache-server \
		--rm

ci-round-table: ci-check ## Run Round Table V2 gate locally
	act push \
		--job round-table-gate \
		--platform ubuntu-latest=$(ACT_RUNNER) \
		--no-cache-server \
		--rm
