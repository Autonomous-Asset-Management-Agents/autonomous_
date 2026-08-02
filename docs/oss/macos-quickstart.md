# autonomous_ on macOS — Quickstart (from source)

> **Scope (read first).** This runs autonomous_ **from source as the web console** — the browser
> dashboard plus the local Python engine. It is **not** the native desktop app: there is no packaged/
> notarized macOS build yet, so features that live in the Electron shell (system tray, auto-start,
> the in-app *Start/Stop engine* buttons, engine auto-restart) are absent — you start/stop the engine
> from the terminal instead. Everything else (dashboard, positions, decisions, the kill switch and the
> sell controls) works, driven over HTTP against the local engine.
>
> **Audience:** technically comfortable users / developers.
>
> **Verified.** Every command below is exercised on each change by GitHub-hosted macOS CI runners
> (`macos-smoke.yml`) that clone the **real public `autonomous_` repo** and run the one-command script
> end-to-end (`doctor` → venv → deps → `setup.sh` → offline boot → `/health 200`). **Apple Silicon**
> (`macos-14`/`macos-15`) is the gate; an **Intel** (`macos-13`) leg runs informationally. Mode A (the
> offline boot) is proven there; Mode B runs the same path with real paper keys + a running Ollama.

## Requirements

- **macOS Apple Silicon (M1–M4) recommended.** Intel Macs: see the [Intel note](#intel-macs) below.
- **Python 3.12** (not 3.8 — the engine needs 3.12): `python3.12 --version`
- **Node.js 18+**: `node --version`
- *(Mode B only)* **Ollama** + a free **Alpaca paper** account

```bash
brew install python@3.12 node git      # + `ollama` for Mode B
```

## Fastest path — one command

```bash
git clone https://github.com/Autonomous-Asset-Management-Agents/autonomous_.git autonomous
cd autonomous
bash mac-quickstart.sh doctor          # check prerequisites first (Python 3.12 / Node / git / network)
bash mac-quickstart.sh                 # pick Quick look (offline) or Paper trading, then it runs the app
```

`mac-quickstart.sh` does the whole flow for you: creates the venv, installs the engine deps (stripping
the Linux-only `+cpu` torch pins), runs `setup.sh`, writes the correct `.env.oss` for your chosen mode,
and starts the engine + web UI. Useful variants:

- `bash mac-quickstart.sh doctor` — re-check your environment only (prints exactly what's missing).
- `bash mac-quickstart.sh --smoke` — boot the engine offline and verify `/health`, then exit (self-test).
- `bash mac-quickstart.sh --offline` / `--paper` — skip the prompt and pick the mode directly.

Prefer to see every step? The **Manual setup** below is exactly what the script automates.

## Two ways to run it

| | Mode A — **Quick look** | Mode B — **Paper trading** |
|---|---|---|
| What you get | Dashboard + engine boot, **recommendation-only** (no orders) | Full paper trading (virtual orders on Alpaca) |
| Alpaca account | **not needed** | free **paper** account required |
| Ollama / local LLM | **not needed** | required |
| How | four lines in `.env.oss` (step ④A) | Ollama + paper keys (step ④B) |

Start with **Mode A** to see it run in ~5 minutes; switch to **Mode B** when you want paper orders.

## Manual setup

> **Layout note.** The public `autonomous_` repo is **flat** — the engine code (`core/`, `config.py`,
> `setup.sh`, `requirements.txt`, `pandas-ta/`) lives at the repo **root**. There is no
> `ai_trading_bot/` subfolder, so every command below runs from the repo root.

### ① Clone
```bash
git clone https://github.com/Autonomous-Asset-Management-Agents/autonomous_.git autonomous
cd autonomous
```

### ② Python 3.12 environment + engine dependencies
```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
# macOS torch/vision/audio ship as PLAIN wheels — the "+cpu" pin is Linux/Windows-only. Strip it:
sed 's/+cpu//g' requirements.txt > requirements.mac.txt
pip install -r requirements.mac.txt
pip install ./pandas-ta          # local indicator package (also at the repo root)
```

### ③ First-time config (generates `.env.oss` + local SQLite)
```bash
bash setup.sh                    # a fresh clone has no exec bit → run via `bash`, not `./setup.sh`
```

### ④ Choose a mode — edit `.env.oss`

**A) Quick look (recommendation-only, no account, no Ollama).** Append these four lines to `.env.oss`.
The engine then boots without any working broker or cloud-LLM credentials; the dashboard works and no
orders are ever sent:
```bash
cat >> .env.oss <<'EOF'
ALPACA_API_KEY=offline_mode
ALPACA_SECRET_KEY=offline_mode
LLM_PROVIDER=ollama
PAPER_TRADING=true
EOF
```
> Why all four? The engine's pre-flight (`shadow_boot`) rejects an **empty** Alpaca secret *before* it
> recognises the `offline_mode` sentinel, so **both** Alpaca keys must be set. `LLM_PROVIDER=ollama`
> keeps it off the cloud-LLM path (the default `gemini` would demand a `GEMINI_API_KEY` the OSS build
> has no reason to hold), and `PAPER_TRADING=true` makes an absent LLM a graceful *degrade* rather
> than a boot-blocking safety abort.

**B) Paper trading.** Point the LLM at local Ollama and add your **paper** keys (Shadow Boot skips the
broker check only for the offline sentinel — for real trading it validates the credentials):
```bash
cat >> .env.oss <<'EOF'
LLM_PROVIDER=ollama
LOCAL_LLM_MODEL=mistral:7b-instruct-v0.3-q4_K_M
OLLAMA_BASE_URL=http://localhost:11434
ALPACA_API_KEY=PK...your-paper-key-id...
ALPACA_SECRET_KEY=...your-paper-secret...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
PAPER_TRADING=true
EOF
```
Then start Ollama + pull the model:
```bash
ollama serve &
ollama pull mistral:7b-instruct-v0.3-q4_K_M      # ~4 GB (on a 16 GB Mac, a lighter model such as
                                                 # `qwen2.5:3b-instruct` leaves more headroom)
```
Free paper account: <https://app.alpaca.markets> (Paper Trading → API keys).

### ⑤ Run
Either the one-command wrapper (starts the web UI + engine + the read-only public API together):
```bash
npm install                      # first run only
npm run desktop:dev
```
…or the engine and the web UI as two separate processes (handy for reading the engine log directly):
```bash
python -m core.engine            # Terminal 1 → API on http://localhost:8001
npm run dev                      # Terminal 2 → vite UI on http://localhost:5173 (proxies to the engine)
```

### ⑥ Open + verify
- Dashboard: **`http://localhost:5173`**  *(not 8000)*
- Engine alive: `curl http://localhost:8001/health` → expect `HTTP 200`.

A healthy **Mode A** boot log shows:
```
Alpaca offline mode detected (ALPACA_API_KEY='offline_mode'). Broker check skipped …
No Enterprise License detected. Booting OSS Community Engine.
BotEngine ready — Cloud Run startup complete.
INFO:     127.0.0.1:xxxxx - "GET /health HTTP/1.1" 200 OK
```
A **Mode B** boot additionally logs `Alpaca API reachable and Auth valid.` and `Ollama API reachable.`
once your paper keys validate and `ollama serve` is running.

## Use on iPhone / iPad (view-mostly)
```bash
npm run dev -- --host        # exposes vite on your Mac's IP
```
Open `http://<MAC-IP>:5173` (IP via `ipconfig getifaddr en0`), then **Share → Add to Home Screen**.
On non-desktop browsers `isDesktop()` is false, so the engine-control cards are hidden — the phone/
tablet is a **dashboard viewer**, not full control.

## Intel Macs

Recent **PyTorch publishes no macOS x86_64 wheels**, so the from-source `torch` install can fail on an
Intel Mac. `bash mac-quickstart.sh doctor` flags this up front. On Intel, use the **Docker path**
([docs/oss/README.md](./README.md)) — it runs the same engine in a Linux container, independent of your
Mac's Python/architecture. Apple-Silicon Macs (M1–M4) run the from-source path above natively.

## Troubleshooting
- **`/health` never returns 200 / engine exits right after "Starting Shadow Boot pre-flight checks"**
  → in Mode A, make sure **all four** lines are in `.env.oss` — in particular **both**
  `ALPACA_API_KEY` *and* `ALPACA_SECRET_KEY` set to `offline_mode` (the secret alone being blank is
  the most common cause). In Mode B, check your paper keys are valid and `ollama serve` is running.
- **`npm run desktop:dev` fails with `cd: ai_trading_bot: No such file or directory`** → your clone
  predates the flat-layout fix; `git pull` (or use the two-terminal split in step ⑤).
- **`ModuleNotFound` / import errors** → wrong Python; must be **3.12** with the venv activated. Also
  confirm `pip install ./pandas-ta` ran.
- **`No matching distribution found for torch==…`** → on Apple Silicon, re-run the
  `sed 's/+cpu//g' requirements.txt` step (the `+cpu` pins must be stripped). On **Intel**, torch has
  no macOS x86_64 wheel — use the [Docker path](#intel-macs).
- **Port 5173 or 8001 already in use** → another server/instance is running.

## What this is *not*
- No native `.dmg` (no `build.mac` target / no macOS build in the release pipeline). A real native
  Mac app (tray, auto-start, notarized install) is a separate, larger effort.
- The engine is managed by the terminal process here, not by an Electron supervisor.
