# Operations & Troubleshooting Playbook

This document is for anyone running the AAAgents Community Edition — whether via **Docker Compose** (`docker-compose.oss.yml`) or in **Native Desktop Mode** (SQLite + LocalStateClient, no Docker required). If the bot fails to start, trade, or log metrics, look here first.

> [!NOTE]
> Sections 1–5 are Docker-specific. Section 6 covers **Native Desktop Mode** (SQLite) issues.

## 1. Container Initialization Failures

### Symptom: Backend Container Exits Immediately with "Connection Refused"
**Cause:** The typical cause is that the `backend` container is trying to run Alembic database migrations before the `postgres` container is actually ready to accept connections.
**Resolution:**
1. Check the logs: `docker compose --env-file .env.oss -f docker-compose.oss.yml logs backend`
2. If you see `psycopg2.OperationalError: FATAL: the database system is starting up`, simply wait 10 seconds and run `docker compose --env-file .env.oss -f docker-compose.oss.yml up -d backend` again.
3. *Note:* The Docker-Compose file includes a `depends_on: condition: service_healthy` check, but some setups resolve this prematurely.

### Symptom: Alembic "Target database is not up to date" (Out of Sync)
**Cause:** Modifying database models and running the system without creating an alembic revision.
**Resolution:**
```bash
# Exec into the backend container
docker exec -it aaagents-backend bash
# Autogenerate the migration
alembic revision --autogenerate -m "Fix out of sync"
# Apply it
alembic upgrade head
```

## Engine running but no trades after 1 hour

If `make logs` shows the engine starting but no orders are placed, walk this checklist:

1. **Are Alpaca keys actually set?**
   ```bash
   docker compose --env-file .env.oss -f docker-compose.oss.yml exec backend printenv ALPACA_API_KEY
   ```
   If output is empty or `offline_mode`, your keys never made it into the env. Open `.env.oss` and confirm `ALPACA_API_KEY=` (no leading `#`) has your real key as the value.

2. **Did the engine boot in Shadow Mode?**
   ```bash
   make logs | grep -i "shadow\|offline_mode\|Alpaca offline"
   ```
   Hits = engine is running without a real broker connection. Fix Step 1.

3. **Did models download?**
   ```bash
   make logs | grep -i "OSS sync complete\|RL agent loaded\|RL agent file not found"
   ```
   If you see `RL agent file not found` and your release has no model assets, you're on a build without the model bundle. The engine boots but the LSTM and RL voting agents return neutral 0.5, which dilutes consensus to ~0.5 — below the BUY threshold (0.65). Wait for a release that ships a model bundle, OR build your own bundle (see [docs/oss/RELEASING_MODEL_BUNDLE.md](./RELEASING_MODEL_BUNDLE.md)).

4. **Is the market open?**
   The default trading loop sleeps 5 minutes when the market clock is closed. Check `make logs | grep "Market is CLOSED\|Sleeping for 5"`. If you want to test off-hours, set `BYPASS_MARKET_HOURS=true` in `.env.oss` (paper trading only).

5. **Did the scanner identify any candidates?**
   ```bash
   make logs | grep "Scanner identified top candidates"
   ```
   If absent, the AI market scanner returned an empty universe. Common causes (in order of likelihood): the Alpaca data feed is rate-limited or returning empty snapshots; scanner volatility/RSI thresholds are filtering everything out for the current regime; or `GEMINI_API_KEY` is unset and the non-Gemini fallback isn't finding enough candidates. The engine retries every cycle — if it never recovers, file a bug. Note: missing `POLYGON_API_KEY` is **not** the cause — `core/market_regime.py` falls through to a SPY-derived implied-volatility path (`USE_SPY_VOLATILITY_FALLBACK=True` default) using Alpaca data, so regime detection keeps working without Polygon.

## 2. LLM & Machine Learning Issues

### Symptom: "GEMINI_API_KEY not found AND Live Trading is active" or Boot Crash
**Cause:** The system relies on Google's Gemini models for sentiment analysis and reasoning. If you are attempting to boot with `PAPER_TRADING=False` (live trading with real money) and the Gemini API key is missing, the system will deterministically abort to prevent unguided live trades.
**Resolution:**
1. Obtain a Gemini API key from Google AI Studio.
2. Edit your `.env.oss` file and add `GEMINI_API_KEY=your_key_here`.
3. Restart the backend container: `docker compose --env-file .env.oss -f docker-compose.oss.yml restart backend`.
*Note: If you only want to test the system without an LLM, ensure `PAPER_TRADING=True`. The system will boot in "Degraded Sentiment Mode" (skipping LLM-dependent agents).*

### Symptom: Docker OOM (Out Of Memory) Crash
**Cause:** Loading the PyTorch RL agents alongside data caching exceeds your Docker Desktop memory allocation limits.
**Resolution:**
1. Open Docker Desktop settings.
2. Increase the Resource limit for Memory to at least **8 GB** (12 GB recommended if also running local LLMs).

## 3. Trading & Execution Blocks

### Symptom: Bot evaluates a BUY but executes a HOLD
**Cause:** The *Iron Dome* blocked the trade. This is expected behavior!
**Resolution:**
Check the logs for `ComplianceGuardian` or `RiskManager`. Typical reasons:
- `PDT Violation Block`: You are trying to day-trade more than 3 times in 5 days with an account under $25,000.
- `VIX Threshold Exceeded`: Market volatility is too high, the bot activated the global Stop-Loss.
- `Insufficient Buying Power`: You lack the cash required for the calculated position size.

## 4. Resetting the System (Nuclear Option)
If everything breaks and you just want to start fresh (deleting all local paper-trading history and state):
```bash
docker compose --env-file .env.oss -f docker-compose.oss.yml down -v
rm -rf data/db
docker compose --env-file .env.oss -f docker-compose.oss.yml up -d
```
*(Warning: The `-v` flag deletes your Postgres docker volumes. Your Alpaca brokerage state remains unaffected).*

## 5. Exposing the Dashboard Over the Internet (SSH Tunnel Required)

The OSS container binds the frontend to `127.0.0.1:80` by default. This is intentional: the dashboard ships your broker API headers and session cookies; on plain HTTP over a VPS or shared network those are visible to every hop in between.

If you want to access the dashboard from another machine (e.g., a VPS), **do not** edit `docker-compose.oss.yml` to bind to `0.0.0.0:80`. This exposes your bot completely.

Instead, use an **SSH Tunnel**. This is the industry standard for securely accessing remote dashboards without the overhead of setting up TLS/Nginx.

### How to set up an SSH Tunnel

From your local machine (your laptop), run the following command to securely forward the dashboard and API ports over SSH:

```bash
ssh -L 8080:localhost:80 -L 8001:localhost:8001 user@vps-ip
```

*(Replace `user@vps-ip` with your actual VPS SSH login credentials).*

Once the SSH session is open, simply open your local web browser and navigate to:
`http://localhost:8080`

The traffic is encrypted via SSH, completely invisible to the public internet, and requires zero additional setup on the server.

### What NOT to do
- Do **not** edit `docker-compose.oss.yml` to bind `"80:8080"` (without `127.0.0.1:`) — that re-exposes plain HTTP to the public.
- Do **not** disable loopback bindings for "just a quick test" — every test request leaks tokens that grant the same access as the real session.

### Alternative: Public Internet Exposure via TLS (Caddy Reverse Proxy)

If you must expose the dashboard to the public internet natively (without SSH), you MUST use a reverse proxy with TLS (like Let's Encrypt). The easiest way is using Caddy.

> [!CAUTION]
> **CRITICAL WARNING: LocalMockAuth bypass!**
> The OSS Community Edition uses `LocalMockAuth` which treats **ANY Bearer token as an admin token** for local operation.
> If you expose the Engine API (`127.0.0.1:8001`) or Dashboard (`127.0.0.1:80`) to the public internet using Caddy without configuring your own authentication layer, **your entire trading bot will be exposed to attackers.**
> You MUST configure Caddy to require Basic Auth or implement your own API Gateway.

1. Keep the `docker-compose.oss.yml` bindings on `127.0.0.1:80` as they are.
2. Install Caddy on your server.
3. Edit `/etc/caddy/Caddyfile` with your public domain (add `basicauth` block):
```caddyfile
bots.your-domain.com {
    basicauth {
        # Generate with: caddy hash-password
        admin JDJhJDE0JH...
    }
    reverse_proxy 127.0.0.1:80
}
```
4. Restart Caddy. It will automatically provision TLS certs via Let's Encrypt and forward public `HTTPS:443` traffic securely to the local container.

## 6. SQLite-Specific Issues (Native Desktop Mode)

These issues apply when running **without Docker** — i.e. `DATABASE_URL` is unset or points to SQLite, and `REDIS_URL` is empty.

### Symptom: "database is locked" errors in logs
**Cause:** SQLite uses file-level locking. If another process (a second engine instance, a DB browser, or a backup tool) holds the file open, concurrent writes will fail.
**Resolution:**
1. Ensure only one engine instance is running: `tasklist | findstr python` (Windows) or `ps aux | grep python` (Linux/macOS).
2. Close any SQLite browser tools (DB Browser, DBeaver) that have the database open.
3. If using the Electron desktop app, check that no orphaned Python sidecar process is running.

### Symptom: Engine fails to start — "DB file not found" or empty data directory
**Cause:** The bootstrap creates `data/aaagents.db` automatically, but the `data/` directory must be writable.
**Resolution:**
1. Ensure the `ai_trading_bot/data/` directory exists and is writable.
2. Check for antivirus software blocking file creation in the project directory.
3. On Windows, ensure the path does not exceed 260 characters (long path limitation).

### Symptom: "Schema version mismatch" warning at startup
**Cause:** The engine detected that the existing SQLite database was created by an older version of the codebase.
**Resolution:**
The bootstrap automatically backs up the database (e.g. `aaagents.db.bak.20260528_213000`) and runs `create_all()` to add new tables. No manual action required. If the backup fails (e.g. `PermissionError`), the engine continues with a warning — the schema update still proceeds.

### Symptom: SQLite database file grows unexpectedly large
**Cause:** WAL (Write-Ahead Log) mode accumulates `aaagents.db-wal` and `aaagents.db-shm` files alongside the main database.
**Resolution:**
1. This is normal — WAL mode provides better concurrent read performance.
2. The WAL file is automatically checkpointed when the engine shuts down cleanly.
3. To force a checkpoint: `sqlite3 data/aaagents.db "PRAGMA wal_checkpoint(TRUNCATE);"`

### Symptom: LocalStateClient data lost after restart
**Cause:** This is **expected behavior**. `LocalStateClient` is an in-memory Redis replacement — all cached data (rate limits, rolling buffers, streams) is ephemeral.
**Resolution:**
No action needed. Persistent data (trades, signals, audit logs) is stored in SQLite. Only transient cache data is lost on restart.

## 7. OS Keychain Issues (SEC-5)

### Symptom: `WARNING — SEC-5: keyring library not installed`
**Cause:** The `keyring` package is not installed in the Python environment.
**Resolution:**
```bash
pip install keyring>=25.6.0
```
After installing, run `python -m core.keychain_cli setup` to configure your API keys. Without `keyring`, the engine falls back to `.env.oss` (plaintext file).

### Symptom: `WARNING — SEC-5: Keychain read failed for ALPACA_API_KEY`
**Cause:** The OS credential backend is unavailable. Common on headless Linux servers without a desktop environment (no D-Bus / Secret Service).
**Resolution:**
- **Linux Desktop:** Ensure `gnome-keyring` or `kwallet` is installed and running.
- **Linux Server / CI:** Use `.env.oss` or explicit environment variables instead. The keychain is designed for desktop users.
- **Windows / macOS:** This should not occur. Check that `keyring` can access the Credential Manager / Keychain:
  ```bash
  python -c "import keyring; keyring.set_password('test', 'test', 'val'); print(keyring.get_password('test', 'test'))"
  ```

### Symptom: Keys were set via `keychain_cli setup` but engine doesn't use them
**Cause:** An explicit environment variable or `.env.oss` entry is overriding the keychain.
**Resolution:** Check the precedence chain:
1. **Explicit env var** (highest priority) — e.g. `ALPACA_API_KEY=xxx` set in shell
2. **OS Keychain** — via `keyring`
3. **`.env.oss`** — via `load_dotenv()`

Run `python -m core.keychain_cli status` to verify which keys are in the keychain.

### Symptom: Want to reset all stored keys
**Resolution:**
```bash
python -m core.keychain_cli delete   # Remove all keys from OS keychain
python -m core.keychain_cli setup    # Re-enter keys
```
On Windows, you can also manage keys directly in *Control Panel → Credential Manager → Generic Credentials → aaagents**.

