#!/usr/bin/env bash
# mac-quickstart.sh — one-command macOS/Linux setup for autonomous_ (web console, from source).
#
# The single supported from-source entry point. Automates the verified path in
# docs/oss/macos-quickstart.md against the FLAT public autonomous_ layout (engine at the repo root —
# no ai_trading_bot/ subfolder):
#   doctor (prereqs) → Python 3.12 venv → deps (requirements.txt, +cpu stripped, ./pandas-ta) →
#   bash setup.sh → Mode A (offline) or Mode B (paper) → engine + `npm run dev` (two processes).
#
# The traps a naive guide falls into (and this script avoids): (a) the offline sentinel needs BOTH
# Alpaca keys set — an empty secret is rejected before the sentinel check; (b) LLM_PROVIDER=ollama,
# else the engine defaults to gemini and demands a GEMINI_API_KEY; (c) NOT `npm run desktop:dev` —
# that script does `cd ai_trading_bot`, which does not exist in the flat public repo.
#
# Usage:
#   bash mac-quickstart.sh                 # interactive: pick offline or paper, then run the app
#   bash mac-quickstart.sh doctor          # check prerequisites only (Python 3.12 / Node / git / net)
#   bash mac-quickstart.sh --offline       # non-interactive offline (recommendation-only) run
#   bash mac-quickstart.sh --paper         # paper trading (needs Ollama + Alpaca paper keys in .env.oss)
#   bash mac-quickstart.sh --smoke         # offline boot + /health check, then exit (self-test / CI)
#
# Both Mode A and the --smoke boot are proven on real macOS runners (Intel + Apple Silicon) by
# .github/workflows/macos-smoke.yml. Functions are unit-testable (see scripts/test_mac_quickstart.sh);
# main() runs only when executed, not when sourced.
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[0;33m'; NC='\033[0m'
info() { printf "${GRN}▸ %s${NC}\n" "$1"; }
warn() { printf "${YEL}⚠ %s${NC}\n" "$1"; }
die()  { printf "${RED}✖ %s${NC}\n" "$1" >&2; exit 1; }

ENV_FILE=".env.oss"
OLLAMA_MODEL="mistral:7b-instruct-v0.3-q4_K_M"
OLLAMA_URL="http://localhost:11434"

# Idempotently set KEY=VALUE in an env file: replace an existing (even commented) KEY= line, else
# append. Handles both GNU sed (Linux / Git Bash) and BSD sed (macOS).
ensure_env_line() {
  local file="$1" key="$2" value="$3"
  touch "$file"
  if grep -qE "^[[:space:]]*#?[[:space:]]*${key}=" "$file"; then
    if sed --version >/dev/null 2>&1; then
      sed -i -E "s|^[[:space:]]*#?[[:space:]]*${key}=.*|${key}=${value}|" "$file"
    else
      sed -i '' -E "s|^[[:space:]]*#?[[:space:]]*${key}=.*|${key}=${value}|" "$file"
    fi
  else
    printf '%s=%s\n' "$key" "$value" >> "$file"
  fi
}

# Read a value for KEY from the env file (empty if unset/commented).
env_value() {
  local file="$1" key="$2"
  [ -f "$file" ] || { echo ""; return; }
  grep -E "^[[:space:]]*${key}=" "$file" | tail -1 | sed -E "s|^[[:space:]]*${key}=||" || true
}

find_python312() {
  for cand in python3.12 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 \
      && "$cand" -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >/dev/null 2>&1; then
      echo "$cand"; return 0
    fi
  done
  return 1
}

# Preflight: verify every prerequisite up front with an actionable message per failure. Returns 0
# when the environment can run the from-source setup, 1 otherwise. Prints an Intel-Mac caveat (recent
# torch ships no macOS x86_64 wheels) but does not fail on it — the deps step is the real proof.
doctor() {
  local ok=1
  info "Environment doctor — checking prerequisites…"

  local py
  if py="$(find_python312)"; then info "Python 3.12: $py ($("$py" --version 2>&1))"
  else warn "Python 3.12 NOT found → install it:  brew install python@3.12"; ok=0; fi

  if command -v node >/dev/null 2>&1; then info "Node: $(node --version)"
  else warn "Node NOT found → install it:  brew install node"; ok=0; fi

  if command -v npm >/dev/null 2>&1; then info "npm: $(npm --version)"
  else warn "npm NOT found (ships with Node) → install it:  brew install node"; ok=0; fi

  if command -v git >/dev/null 2>&1; then info "git: $(git --version | awk '{print $3}')"
  else warn "git NOT found → install the Xcode command-line tools:  xcode-select --install"; ok=0; fi

  local os arch; os="$(uname)"; arch="$(uname -m)"
  if [ "$os" = "Darwin" ] && [ "$arch" = "x86_64" ]; then
    warn "Intel Mac (x86_64): recent PyTorch publishes NO macOS x86_64 wheels, so the from-source"
    warn "  torch install below may fail. On Intel Macs prefer the Docker path (docs/oss/README.md)."
  else
    info "Platform: $os/$arch"
  fi

  # ~6 GB is comfortable for the venv + wheels (torch is large); warn, don't block, if tight.
  local free_kb
  if free_kb="$(df -Pk . 2>/dev/null | awk 'NR==2{print $4}')" && [ -n "${free_kb:-}" ]; then
    if [ "$free_kb" -lt 6000000 ]; then warn "Low free disk (~$((free_kb/1024)) MB) — engine deps need several GB."
    else info "Free disk: ~$((free_kb/1048576)) GB"; fi
  fi

  if curl -fsS -m 8 https://pypi.org/simple/ >/dev/null 2>&1; then info "PyPI reachable"
  else warn "PyPI not reachable — check your network / proxy (pip install will fail)."; ok=0; fi
  if curl -fsS -m 8 https://github.com >/dev/null 2>&1; then info "GitHub reachable"
  else warn "GitHub not reachable — check your network / proxy (git clone will fail)."; ok=0; fi

  if [ "$ok" = "1" ]; then info "Doctor: prerequisites OK."; return 0
  else warn "Doctor: prerequisites missing (see ⚠ above)."; return 1; fi
}

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

main() {
  # ---- argument parsing --------------------------------------------------------------------------
  local noninteractive=0 mode="" smoke=0
  while [ $# -gt 0 ]; do
    case "$1" in
      doctor)            doctor; exit $? ;;
      --offline)         mode="offline" ;;
      --paper)           mode="paper" ;;
      --non-interactive|-y) noninteractive=1 ;;
      --smoke)           smoke=1; noninteractive=1; [ -z "$mode" ] && mode="offline" ;;
      -h|--help)         usage; exit 0 ;;
      *)                 die "Unknown option: $1  (try: doctor | --offline | --paper | --smoke | --help)" ;;
    esac
    shift
  done

  [ -f "setup.py" ] || die "Run this from the autonomous_ root (setup.py not found)."

  # ---- preflight ---------------------------------------------------------------------------------
  doctor || die "Fix the prerequisites above, then re-run. (Run 'bash mac-quickstart.sh doctor' to re-check.)"

  # ---- Python 3.12 venv + engine deps ------------------------------------------------------------
  local PY; PY="$(find_python312)" || die "Python 3.12 not found. Install it: brew install python@3.12"
  if [ ! -d ".venv" ]; then info "Creating .venv…"; "$PY" -m venv .venv; fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
  info "Installing engine dependencies (this can take a few minutes)…"
  # requirements.txt is at the repo root; on macOS the "+cpu" torch pins are Linux/Windows-only.
  if [ "$(uname)" = "Darwin" ]; then
    sed 's/+cpu//g' requirements.txt | pip install -q -r /dev/stdin
  else
    pip install -q -r requirements.txt
  fi
  pip install -q ./pandas-ta

  # ---- first-time config (fresh clone has no exec bit → run via `bash`, not `./setup.sh`) ---------
  [ -f "$ENV_FILE" ] || { info "Running setup.sh…"; bash setup.sh --non-interactive; }

  # ---- choose a run mode -------------------------------------------------------------------------
  local existing_ak; existing_ak="$(env_value "$ENV_FILE" ALPACA_API_KEY)"
  if [ -z "$mode" ]; then
    if [ -n "$existing_ak" ] && [ "$existing_ak" != "offline_mode" ]; then
      mode="paper" # real keys already present
    elif [ "$noninteractive" = "1" ]; then
      mode="offline"
    else
      read -r -p "Run mode — [p]aper trading (Alpaca paper keys + Ollama) or [q]uick look (offline, no account)? [q]: " M
      case "$M" in p | P | paper | Paper) mode="paper" ;; *) mode="offline" ;; esac
    fi
  fi

  if [ "$mode" = "offline" ]; then
    info "Quick look (offline / recommendation-only) — no Alpaca account, no Ollama, no orders."
    # BOTH Alpaca keys must be the sentinel (an empty secret is rejected before the sentinel check);
    # LLM→ollama keeps it off the cloud-LLM path; PAPER_TRADING keeps an absent LLM a graceful degrade.
    ensure_env_line "$ENV_FILE" "ALPACA_API_KEY" "offline_mode"
    ensure_env_line "$ENV_FILE" "ALPACA_SECRET_KEY" "offline_mode"
    ensure_env_line "$ENV_FILE" "LLM_PROVIDER" "ollama"
    ensure_env_line "$ENV_FILE" "PAPER_TRADING" "true"
  else
    info "Paper trading: LLM → Ollama (the gemini-default fix) + Alpaca paper keys."
    ensure_env_line "$ENV_FILE" "LLM_PROVIDER" "ollama"
    ensure_env_line "$ENV_FILE" "LOCAL_LLM_MODEL" "$OLLAMA_MODEL"
    ensure_env_line "$ENV_FILE" "OLLAMA_BASE_URL" "$OLLAMA_URL"
    command -v ollama >/dev/null 2>&1 || die "Ollama not installed (needed for paper mode): brew install ollama"
    pgrep -x ollama >/dev/null 2>&1 || { info "Starting ollama serve…"; ollama serve >/dev/null 2>&1 & sleep 2; }
    info "Pulling $OLLAMA_MODEL (~4 GB, once)…"; ollama pull "$OLLAMA_MODEL"
    ensure_env_line "$ENV_FILE" "PAPER_TRADING" "true"
    ensure_env_line "$ENV_FILE" "ALPACA_BASE_URL" "https://paper-api.alpaca.markets"
    if [ -z "$existing_ak" ]; then
      [ "$noninteractive" = "1" ] && die "Paper mode needs ALPACA_API_KEY / ALPACA_SECRET_KEY in .env.oss (none found)."
      warn "Alpaca PAPER keys required (free account: https://app.alpaca.markets)."
      read -r -p "  ALPACA_API_KEY (PK…): " AK
      read -r -s -p "  ALPACA_SECRET_KEY: " AS; echo
      [ -n "$AK" ] && [ -n "$AS" ] || die "Both Alpaca paper keys are required for paper mode."
      ensure_env_line "$ENV_FILE" "ALPACA_API_KEY" "$AK"
      ensure_env_line "$ENV_FILE" "ALPACA_SECRET_KEY" "$AS"
    fi
  fi

  # ---- --smoke: boot the engine, poll /health, exit (self-test / CI — no web UI) -----------------
  if [ "$smoke" = "1" ]; then
    info "Smoke: booting engine + polling http://localhost:8001/health (no web UI)…"
    local log="${TMPDIR:-/tmp}/aaa-engine-smoke.log"
    python -m core.engine >"$log" 2>&1 &
    local epid=$! ok=0 code
    for i in $(seq 1 40); do
      sleep 3
      code="$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8001/health || true)"
      info "  t=$((i*3))s /health=$code"
      [ "$code" = "200" ] && { ok=1; break; }
      kill -0 "$epid" 2>/dev/null || { warn "engine exited early"; break; }
    done
    echo "----- engine log (tail) -----"; tail -40 "$log" || true
    kill "$epid" 2>/dev/null || true
    [ "$ok" = "1" ] || die "Engine did not serve /health 200 (smoke)."
    info "✅ Smoke passed — engine booted (offline) + /health 200."
    return 0
  fi

  # ---- frontend deps + run: engine (background) + vite (foreground) -------------------------------
  # NOT `npm run desktop:dev` — that script does `cd ai_trading_bot`, absent in the flat public repo.
  info "Installing frontend dependencies…"; npm install
  info "Starting the engine on :8001…"
  python -m core.engine &
  local engine_pid=$!
  trap 'kill "$engine_pid" 2>/dev/null || true' EXIT INT TERM
  info "Starting the web UI → http://localhost:5173 (engine on :8001). Ctrl+C to stop both."
  npm run dev
}

# Run main only when executed directly (so tests can source the functions).
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  main "$@"
fi
