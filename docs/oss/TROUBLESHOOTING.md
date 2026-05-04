# Operations & Troubleshooting Playbook

This document is for anyone running the AAAgents Community Edition locally via Docker Compose (`docker-compose.oss.yml`). If the bot fails to start, trade, or log metrics, look here first.

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

## 2. LLM & Machine Learning Issues

### Symptom: "TimeoutError: Ollama Connection Refused"
**Cause:** The system is configured to use a local LLM via Ollama (`http://localhost:11434`), but Ollama is not running on your host machine or doesn't have the required model.
**Resolution:** 
1. Ensure Ollama is installed on your host OS.
2. Run `ollama serve` in a terminal.
3. Download the correct model required by the bot (default is `llama3.2`): `ollama pull llama3.2`.
4. Ensure your docker container can reach the host (`host.docker.internal` should be used instead of `localhost` in the `.env` file under `OLLAMA_BASE_URL`).

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

1. Keep the `docker-compose.oss.yml` bindings on `127.0.0.1:80` as they are.
2. Install Caddy on your server.
3. Edit `/etc/caddy/Caddyfile` with your public domain:
```caddyfile
bots.your-domain.com {
    reverse_proxy 127.0.0.1:80
}
```
4. Restart Caddy. It will automatically provision TLS certs via Let's Encrypt and forward public `HTTPS:443` traffic securely to the local container.
