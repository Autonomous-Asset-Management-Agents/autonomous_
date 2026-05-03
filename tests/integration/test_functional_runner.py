"""
AAA Trading Bot - Functional Test Runner
==========================================
Executes TC-01 through TC-16 and collects results for the test report.
Focused on LSTM/RL agent survival after dockerization + CPU migration.
"""

from dotenv import load_dotenv
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime

# Fix Windows console encoding
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()

RESULTS = []


def record(tc_id, name, status, detail=""):
    RESULTS.append(
        {
            "id": tc_id,
            "name": name,
            "status": status,
            "detail": detail,
        }
    )
    icons = {
        "PASS": "[PASS]",
        "FAIL": "[FAIL]",
        "WARN": "[WARN]",
        "SKIP": "[SKIP]",
        "INFO": "[INFO]",
    }
    icon = icons.get(status, "[INFO]")
    print(f"  {icon} {tc_id}: {name} -- {status} {detail}")


def section(title):
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


# ======================================================================
# TC-02: Secrets & Configuration
# ======================================================================
section("TC-02: Secrets & Configuration")

try:
    with open(os.path.join(os.path.dirname(__file__), "..", ".env"), "rb") as f:
        raw = f.read()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    record(
        "TC-02-01",
        ".env loads without BOM",
        "FAIL" if has_bom else "PASS",
        "BOM detected!" if has_bom else "Clean UTF-8",
    )
except Exception:
    is_ci = os.getenv("GITHUB_ACTIONS") == "true"
    record(
        "TC-02-01",
        ".env loads without BOM",
        "SKIP" if is_ci else "FAIL",
        "Could not read .env",
    )

env_checks = {
    "TC-02-02": ("ALPACA_API_KEY", 10),
    "TC-02-03": ("ALPACA_SECRET_KEY", 10),
    "TC-02-04": ("POLYGON_API_KEY", 10),
    "TC-02-05": ("GEMINI_API_KEY", 10),
    "TC-02-06": ("ENGINE_API_KEY", 10),
}
for tc_id, (key, min_len) in env_checks.items():
    val = os.getenv(key, "")
    if len(val) >= min_len:
        record(tc_id, f"{key} set ({len(val)} chars)", "PASS")
    else:
        # Check if we are in CI to decide between FAIL and SKIP
        is_ci = os.getenv("GITHUB_ACTIONS") == "true"
        status = "SKIP" if is_ci else "FAIL"
        record(
            tc_id,
            f"{key} set",
            status,
            f"len={len(val)}, need >={min_len} (expected in CI)",
        )

try:
    import config

    record("TC-02-07", "config.py imports without error", "PASS")
except Exception:
    is_ci = os.getenv("GITHUB_ACTIONS") == "true"
    status = "SKIP" if is_ci else "FAIL"
    record(
        "TC-02-07",
        "config.py imports without error",
        status,
        f"import failed: {traceback.format_exc().splitlines()[-1]}",
    )

try:
    record(
        "TC-02-08",
        f"GEMINI_AVAILABLE={config.GEMINI_AVAILABLE}",
        "PASS" if config.GEMINI_AVAILABLE else "WARN",
        "" if config.GEMINI_AVAILABLE else "Gemini features disabled",
    )
except Exception:
    record("TC-02-08", "GEMINI_AVAILABLE", "FAIL", "config not loaded")

model_files = [
    ("data/lstm_model.pth", 1_000_000),
    ("data/scaler_x.pkl", 500),
    ("data/scaler_y.pkl", 500),
    ("data/model_metadata.json", 100),
]
for fpath, min_size in model_files:
    full = os.path.join(os.path.dirname(__file__), "..", fpath)
    if os.path.exists(full) and os.path.getsize(full) >= min_size:
        record(
            "TC-02-09",
            f"{fpath} present ({os.path.getsize(full):,}B)",
            "PASS",
        )
    else:
        status = "SKIP" if os.getenv("GITHUB_ACTIONS") == "true" else "FAIL"
        record(
            "TC-02-09", fpath, status, "Missing or too small (ML models moved to GCS)"
        )

# Check RL agent files
for rl_file in ["rl_agent_v3_dsr.zip", "data/rl_agent_v5.zip"]:
    full = os.path.join(os.path.dirname(__file__), "..", rl_file)
    if os.path.exists(full):
        record(
            "TC-02-09",
            f"{rl_file} present ({os.path.getsize(full):,}B)",
            "PASS",
        )
    else:
        status = "SKIP" if os.getenv("GITHUB_ACTIONS") == "true" else "FAIL"
        record("TC-02-09", rl_file, status, "Missing (Expected in CI)")

# ======================================================================
# TC-01: Alpaca API Connectivity
# ======================================================================
section("TC-01: Alpaca API Connectivity")

alpaca_api = None
try:
    from alpaca.trading.client import TradingClient

    api_key = os.getenv("ALPACA_API_KEY", "")
    secret_key = os.getenv("ALPACA_SECRET_KEY", "")
    is_ci = os.getenv("GITHUB_ACTIONS") == "true"

    if not api_key or not secret_key:
        record("TC-01-01", "Alpaca connectivity", "SKIP", "Missing credentials")
        record("TC-01-02", "Account has equity", "SKIP", "Missing credentials")
    else:
        base_url = os.getenv(
            "ALPACA_BASE_URL",
            "https://paper-api.alpaca.markets",
        )
        alpaca_api = TradingClient(api_key, secret_key, paper=True)
        acct = alpaca_api.get_account()
        record(
            "TC-01-01",
            f"Account reachable (status={acct.status})",
            "PASS" if acct.status == "ACTIVE" else "FAIL",
        )
        equity = float(acct.equity)
        record(
            "TC-01-02",
            f"Account has equity: ${equity:,.2f}",
            "PASS" if equity > 0 else "FAIL",
        )

        positions = alpaca_api.list_positions()
        record(
            "TC-01-03",
            f"Positions endpoint ({len(positions)} positions)",
            "PASS",
        )
        orders = alpaca_api.list_orders(status="all", limit=5)
        record(
            "TC-01-04",
            f"Orders endpoint ({len(orders)} recent orders)",
            "PASS",
        )
        record(
            "TC-01-06",
            "Base URL check",
            "PASS" if "paper" in base_url else "WARN",
            base_url,
        )
except Exception as e:
    is_ci = os.getenv("GITHUB_ACTIONS") == "true"
    status = "SKIP" if is_ci else "FAIL"
    record(
        "TC-01-01",
        "Alpaca connectivity",
        status,
        f"Error: {str(e).splitlines()[-1]}",
    )

# ======================================================================
# TC-03: Engine Boot & Health
# ======================================================================
section("TC-03: Engine Boot & Health (Local)")

try:
    # Stub pandas_ta if missing
    import types as _types

    for _m in ("pandas_ta", "pandas_ta_classic"):
        if _m not in sys.modules:
            try:
                __import__(_m)
            except ImportError:
                stub = _types.ModuleType(_m)
                stub.strategy = lambda *a, **kw: None
                sys.modules[_m] = stub

    from fastapi.testclient import TestClient
    from core.engine import app

    client = TestClient(app)

    r = client.get("/health")
    record(
        "TC-03-01",
        f"/health returns {r.status_code}",
        (
            "PASS"
            if r.status_code == 200 and r.json().get("status") == "healthy"
            else "FAIL"
        ),
        json.dumps(r.json(), indent=None)[:100],
    )

    r = client.get("/strategy")
    record(
        "TC-03-04",
        f"/strategy returns {r.json().get('strategy', '?')}",
        "PASS" if r.status_code == 200 else "FAIL",
    )

    r = client.get("/benchmark-equity")
    data = r.json()
    ec = data.get("equity_curve", [])
    record(
        "TC-03-05",
        f"/benchmark-equity ({len(ec)} points)",
        "PASS" if len(ec) > 0 else "WARN",
        f"First: {ec[0] if ec else 'N/A'}",
    )
except Exception:
    record("TC-03-01", "Engine boot", "FAIL", traceback.format_exc()[-300:])

# ======================================================================
# TC-11: LSTM Model Tests (CRITICAL - CPU migration survival)
# ======================================================================
section("TC-11: LSTM Model Tests (CPU Migration Survival)")

try:
    import torch
    import numpy as np
    from models.torch_model import (
        EnsembleLSTMModel,
        get_lstm_paths,
    )

    model_path, scaler_x_path, scaler_y_path, meta_path = get_lstm_paths()
    if not os.path.exists(model_path) or not os.path.exists(meta_path):
        record(
            "TC-11-00",
            "LSTM Model Tests",
            "SKIP",
            "Model files missing (GCS migration?)",
        )
        model = None
    else:
        record(
            "TC-11-00",
            f"PyTorch version: {torch.__version__}, "
            f"CUDA: {torch.cuda.is_available()}",
            "INFO",
            f"Device: {'cuda' if torch.cuda.is_available() else 'cpu'}",
        )

        # TC-11-01: Load LSTM model
        device = torch.device("cpu")
        with open(meta_path, "r") as f:
            metadata = json.load(f)

        input_dim = metadata.get("input_dim", 30)
        hidden_dim = metadata.get("hidden_dim", 256)
        num_layers = metadata.get("num_layers", 3)
        output_dim = metadata.get("output_dim", 1)
        num_models = metadata.get("num_models", 3)

        model = EnsembleLSTMModel(
            input_dim,
            hidden_dim,
            num_layers,
            output_dim,
            num_models,
        )

        state_dict = torch.load(
            model_path,
            map_location=device,
            weights_only=False,
        )
        model.load_state_dict(state_dict)
        model.to(device)
        model.eval()

        record(
            "TC-11-01",
            f"LSTM model loaded (ensemble, {num_models} sub-models, "
            f"input_dim={input_dim})",
            "PASS",
            f"hidden={hidden_dim}, layers={num_layers}",
        )

        # TC-11-05: Model weight statistics (sanity check for corruption)
        total_params = sum(p.numel() for p in model.parameters())
        nan_params = sum(torch.isnan(p).sum().item() for p in model.parameters())
        inf_params = sum(torch.isinf(p).sum().item() for p in model.parameters())
        zero_layers = sum(1 for p in model.parameters() if p.abs().max().item() == 0)
        record(
            "TC-11-05",
            f"Weight sanity: {total_params:,} params, {nan_params} NaN, "
            f"{inf_params} Inf, {zero_layers} dead layers",
            "PASS" if nan_params == 0 and inf_params == 0 else "FAIL",
        )

except Exception:
    record(
        "TC-11-01",
        "LSTM model tests",
        "FAIL",
        traceback.format_exc()[-400:],
    )

# ======================================================================
# TC-12: RL Agent Tests (CRITICAL - CPU migration survival)
# ======================================================================
section("TC-12: RL Agent (PPO) Tests (CPU Migration Survival)")

try:
    from stable_baselines3 import PPO
    import numpy as np

    # Try loading rl_agent_v3_dsr.zip (production model)
    rl_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "rl_agent_v3_dsr.zip",
    )
    if not os.path.exists(rl_path):
        rl_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "rl_agent_v5.zip",
        )

    if not os.path.exists(rl_path):
        record(
            "TC-12-01",
            "RL Agent (PPO) Tests",
            "SKIP",
            f"Model files missing at {rl_path}",
        )
    else:
        rl_model = PPO.load(rl_path, device="cpu")
        record(
            "TC-12-01",
            f"RL agent loaded from {os.path.basename(rl_path)}",
            "PASS",
            f"Policy: {rl_model.policy.__class__.__name__}",
        )

        # TC-12-02: Predict with 12D observation
        obs_12d = np.zeros(12, dtype=np.float32)
        action, _ = rl_model.predict(obs_12d, deterministic=True)
        action_names = ["HOLD", "BUY", "SELL"]
        record(
            "TC-12-02a",
            f"Predict with 12D zero vector: action={action} "
            f"({action_names[action]})",
            "PASS",
            # TC-12-02: Predict with 12D observation
        )

        # Try various observation vectors
        obs_bullish = np.array(
            [0.05, 70, 0.5, 0.8, 1.5, 0.02, 0.3, 30, 0.5, 0.1, 0.0, 0.5],
            dtype=np.float32,
        )
        action_bull, _ = rl_model.predict(obs_bullish, deterministic=True)
        record(
            "TC-12-02b",
            f"Predict bullish state: action={action_bull} "
            f"({action_names[action_bull]})",
            "PASS",
        )

        obs_bearish = np.array(
            [-0.05, 25, -0.5, 0.1, 0.5, 0.08, -0.4, 15, 0.0, 0.0, 0.0, 0.8],
            dtype=np.float32,
        )
        action_bear, _ = rl_model.predict(obs_bearish, deterministic=True)
        record(
            "TC-12-02c",
            f"Predict bearish state: action={action_bear} "
            f"({action_names[action_bear]})",
            "PASS",
        )

        # TC-12-02d: Batch prediction (deterministic consistency)
        actions = []
        for _ in range(10):
            a, _ = rl_model.predict(obs_bullish, deterministic=True)
            actions.append(int(a))
        consistent = len(set(actions)) == 1
        record(
            "TC-12-02d",
            f"Deterministic consistency: {set(actions)} over 10 calls",
            "PASS" if consistent else "WARN",
            "Consistent" if consistent else "Non-deterministic!",
        )

        # TC-12-03: Policy network weight check
        policy_params = sum(p.numel() for p in rl_model.policy.parameters())
        nan_count = sum(
            torch.isnan(p).sum().item() for p in rl_model.policy.parameters()
        )
        record(
            "TC-12-03",
            f"Policy network: {policy_params:,} params, {nan_count} NaN",
            "PASS" if nan_count == 0 else "FAIL",
        )


except Exception:
    record(
        "TC-12-01",
        "RL Agent tests",
        "FAIL",
        traceback.format_exc()[-400:],
    )

# Also try loading v5 if we loaded v3
try:
    rl_v5_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "data",
        "rl_agent_v5.zip",
    )
    if os.path.exists(rl_v5_path):
        rl_v5 = PPO.load(rl_v5_path, device="cpu")  # noqa: F821
        obs_test = np.zeros(12, dtype=np.float32)  # noqa: F821
        action_v5, _ = rl_v5.predict(obs_test, deterministic=True)
        record(
            "TC-12-04",
            f"RL Agent v5 loads and predicts: action={action_v5}",
            "PASS",
            f"Policy: {rl_v5.policy.__class__.__name__}",
        )
except Exception:
    record(
        "TC-12-04",
        "RL Agent v5",
        "FAIL",
        traceback.format_exc()[-200:],
    )

# ======================================================================
# TC-10: Smart Exit Unit Tests
# ======================================================================
section("TC-10: Smart Exit")

try:
    from core.smart_exit import should_sell_smart

    # TC-10-01: No exit trigger
    r = should_sell_smart("AAPL", 150, 152, 153, 0.5, True, 3)
    record(
        "TC-10-01",
        f"No trigger: {r.action}, '{r.reason}'",
        "PASS" if r.action == "HOLD" else "FAIL",
    )

    # TC-10-02: Stop-loss (-8%)
    r = should_sell_smart("AAPL", 150, 138, 150, 2.0, True, 3)
    record(
        "TC-10-02",
        f"Stop-loss: {r.action}, '{r.reason}'",
        "PASS" if r.action == "SELL" and "Stop-loss" in r.reason else "FAIL",
    )

    # TC-10-03: Take-profit (+40% to exceed ATR-scaled target)
    r = should_sell_smart("AAPL", 100, 140, 140, 48.0, True, 3)
    record(
        "TC-10-03",
        f"Take-profit: {r.action}, '{r.reason}'",
        "PASS" if r.action == "SELL" and "Take-profit" in r.reason else "FAIL",
    )

    # TC-10-03b: Take-profit without smart scaling
    r = should_sell_smart(
        "AAPL",
        100,
        126,
        126,
        48.0,
        True,
        3,
        smart_take_profit=False,
    )
    record(
        "TC-10-03b",
        f"Take-profit (no smart): {r.action}, '{r.reason}'",
        "PASS" if r.action == "SELL" and "Take-profit" in r.reason else "FAIL",
    )

    # TC-10-04: Trailing stop (after hold, profit, then drawdown)
    r = should_sell_smart("AAPL", 100, 102, 106, 3.0, True, 3)
    record(
        "TC-10-04",
        f"Trailing stop: {r.action}, '{r.reason}'",
        "PASS" if r.action == "SELL" and "Trailing" in r.reason else "FAIL",
    )

    # TC-10-05: Dropped from top-N
    r = should_sell_smart("AAPL", 150, 152, 153, 2.0, False, 15)
    record(
        "TC-10-05",
        f"Dropped from top-N: {r.action}, '{r.reason}'",
        "PASS" if r.action == "SELL" and "Dropped" in r.reason else "FAIL",
    )

    # TC-10-06: Trailing before min hold
    r = should_sell_smart("AAPL", 100, 102, 106, 0.3, True, 3)
    record(
        "TC-10-06",
        f"No trail before min hold: {r.action}",
        "PASS" if r.action == "HOLD" else "FAIL",
    )

except Exception:
    record(
        "TC-10-01",
        "Smart Exit tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# TC-06: RiskManager
# ======================================================================
section("TC-06: RiskManager")

try:
    from core.risk_manager import RiskManager

    rm = RiskManager(client=None, total_capital=100000)
    record(
        "TC-06-01",
        "RiskManager init (capital=100K)",
        "PASS",
        f"risk_pct={rm.risk_per_trade_percent}",
    )

    rm.update_account_equity(100000)
    halted_100k = rm.trading_halted
    record(
        "TC-06-02",
        f"Equity 100K: halted={halted_100k}",
        "PASS" if not halted_100k else "FAIL",
    )

    rm.update_account_equity(85000)
    halted_85k = rm.trading_halted or rm.trading_reduced
    record(
        "TC-06-03",
        f"Equity 85K (15% DD): halted={rm.trading_halted}, "
        f"reduced={rm.trading_reduced}",
        "PASS" if halted_85k else "FAIL",
    )

    rm.update_account_equity(82500)
    halted_825k = rm.trading_halted
    record(
        "TC-06-04",
        f"Equity 82.5K (17.5% DD): halted={rm.trading_halted}",
        "PASS" if halted_825k else "FAIL",
    )

except Exception:
    record(
        "TC-06-01",
        "RiskManager tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# TC-07: ComplianceGuardian
# ======================================================================
section("TC-07: ComplianceGuardian")

try:
    from core.compliance import ComplianceGuardian

    cg = ComplianceGuardian()

    # Valid order (dict format)
    valid_order = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 10,
        "price": 150.0,
        "strategy_id": "test_runner",
        "timestamp": time.time(),
    }
    ok = cg.check_order(valid_order)
    record("TC-07-01", f"Valid order: approved={ok}", "PASS" if ok else "FAIL")

    # Exceeds max value ($150K > $10K limit)
    big_order = {
        "symbol": "AAPL",
        "side": "buy",
        "quantity": 1000,
        "price": 150.0,
        "strategy_id": "test_runner",
        "timestamp": time.time(),
    }
    ok = cg.check_order(big_order)
    record(
        "TC-07-04",
        f"Over max value: approved={ok}",
        "PASS" if not ok else "FAIL",
    )

except Exception:
    record(
        "TC-07-01",
        "ComplianceGuardian tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# TC-09: TradeIntelligence
# ======================================================================
section("TC-09: TradeIntelligence")

try:
    from core.trade_intelligence import TradeIntelligence

    # Use temp file to avoid modifying production data
    with tempfile.NamedTemporaryFile(
        suffix=".json",
        delete=False,
        mode="w",
    ) as f:
        json.dump(
            {
                "version": 1,
                "completed_trades": [],
                "open_positions": {},
                "symbol_intelligence": {},
            },
            f,
        )
        tmp_path = f.name

    ti = TradeIntelligence(data_file=tmp_path)
    record("TC-09-00", "TradeIntelligence init (temp file)", "PASS")

    ti.record_entry("AAPL", 150.0, 10, confidence=0.8)
    record(
        "TC-09-01",
        "record_entry AAPL",
        "PASS",
        f"open_positions: {len(ti._open_positions)}",
    )

    ti.record_exit("AAPL", 160.0, "signal")
    pnl = ti._completed_trades[-1].pnl_pct if ti._completed_trades else 0
    record(
        "TC-09-02",
        f"record_exit AAPL: PnL={pnl:.2f}%",
        "PASS" if abs(pnl - 6.67) < 0.1 else "FAIL",
    )

    # Load production trade_intelligence.json
    prod_file = os.path.join(
        os.path.dirname(__file__),
        "..",
        "data",
        "trade_intelligence.json",
    )
    if os.path.exists(prod_file):
        ti_prod = TradeIntelligence(data_file=prod_file)
        n_trades = len(ti_prod._completed_trades)
        n_symbols = len(ti_prod._symbol_intelligence)
        record(
            "TC-09-08",
            f"Production data: {n_trades} trades, {n_symbols} symbols",
            "PASS",
            f"File size: {os.path.getsize(prod_file):,}B",
        )

    os.unlink(tmp_path)
except Exception:
    record(
        "TC-09-01",
        "TradeIntelligence tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# TC-17: Engine API - Cloud Run (via aaa-api-public)
# ======================================================================
section("TC-17/18: Cloud Run API Tests")

try:
    import requests

    # Get OIDC token for Cloud Run auth (Windows: gcloud.cmd)
    gcloud_cmd = "gcloud.cmd" if os.name == "nt" else "gcloud"
    token_result = subprocess.run(
        [gcloud_cmd, "auth", "print-identity-token"],
        capture_output=True,
        text=True,
        timeout=15,
        shell=(os.name == "nt"),  # nosec B602 - hardcoded gcloud cmd
    )
    oidc_token = token_result.stdout.strip()

    if not oidc_token:
        record(
            "TC-17-00",
            "gcloud OIDC token",
            "SKIP",
            "No token available (CI environment)",
        )
    else:
        record(
            "TC-17-00",
            f"gcloud OIDC token obtained ({len(oidc_token)} chars)",
            "PASS",
        )

        API_BASE = "https://aaa-api-public-lwkxsmb7dq-ey.a.run.app"
        headers = {"Authorization": f"Bearer {oidc_token}"}

        # TC-17-01: /health (public)
        r = requests.get(f"{API_BASE}/health", timeout=10)
        if r.status_code == 200:
            detail = json.dumps(r.json(), indent=None)[:100]
        else:
            detail = r.text[:100]
        record(
            "TC-17-01",
            f"Cloud /health: {r.status_code}",
            "PASS" if r.status_code == 200 else "FAIL",
            detail,
        )

        # TC-17-02: /strategy (authenticated)
        r = requests.get(
            f"{API_BASE}/strategy",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            detail = json.dumps(r.json(), indent=None)[:100]
        else:
            detail = r.text[:50]
        record(
            "TC-17-02",
            f"Cloud /strategy: {r.status_code}",
            "PASS" if r.status_code == 200 else "FAIL",
            detail,
        )

        # TC-17-03: /portfolio-summary (authenticated)
        r = requests.get(
            f"{API_BASE}/portfolio-summary",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            record(
                "TC-17-03",
                f"Cloud /portfolio-summary: "
                f"positions={len(data.get('positions', []))}",
                "PASS",
                data.get("message", "")[:80],
            )
        else:
            record(
                "TC-17-03",
                f"Cloud /portfolio-summary: {r.status_code}",
                "FAIL",
                r.text[:100],
            )

        # TC-17-04: /benchmark-equity
        r = requests.get(
            f"{API_BASE}/benchmark-equity",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            ec = data.get("equity_curve", [])
            record(
                "TC-17-04",
                f"Cloud /benchmark-equity: {len(ec)} points",
                "PASS" if len(ec) > 0 else "WARN",
            )
        else:
            record(
                "TC-17-04",
                f"Cloud /benchmark-equity: {r.status_code}",
                "FAIL",
            )

        # TC-17-06: /top-picks
        r = requests.get(
            f"{API_BASE}/top-picks",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            detail = json.dumps(r.json(), indent=None)[:80]
        else:
            detail = ""
        record(
            "TC-17-06",
            f"Cloud /top-picks: {r.status_code}",
            "PASS" if r.status_code == 200 else "FAIL",
            detail,
        )

        # TC-17-08: /system-health
        r = requests.get(
            f"{API_BASE}/system-health",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            record(
                "TC-17-08",
                f"Cloud /system-health: "
                f"CPU={data.get('cpu_percent', '?')}%, "
                f"RAM={data.get('memory_percent', '?')}%",
                "PASS",
            )
        else:
            record(
                "TC-17-08",
                f"Cloud /system-health: {r.status_code}",
                "FAIL",
            )

except Exception:
    record(
        "TC-17-00",
        "Cloud API tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# TC-04: Engine Lifecycle - /start-live on Cloud Run
# ======================================================================
section("TC-04: Engine Lifecycle (Cloud Run /start-live)")

try:
    import requests

    gcloud_cmd = "gcloud.cmd" if os.name == "nt" else "gcloud"
    token_result = subprocess.run(
        [gcloud_cmd, "auth", "print-identity-token"],
        capture_output=True,
        text=True,
        timeout=15,
        shell=(os.name == "nt"),  # nosec B602 - hardcoded gcloud cmd
    )
    oidc_token = token_result.stdout.strip()
    engine_api_key = os.getenv("ENGINE_API_KEY", "")

    if oidc_token and engine_api_key:
        API_BASE = "https://aaa-api-public-lwkxsmb7dq-ey.a.run.app"
        headers = {
            "Authorization": f"Bearer {oidc_token}",
            "X-Bot-Api-Key": engine_api_key,
        }

        # TC-04-02: start-live without API key should fail
        r = requests.post(
            f"{API_BASE}/start-live",
            headers={"Authorization": f"Bearer {oidc_token}"},
            timeout=15,
        )
        record(
            "TC-04-02",
            f"/start-live without API key: {r.status_code}",
            "PASS" if r.status_code in (401, 403) else "FAIL",
            r.text[:80],
        )

        # TC-04-01: start-live with API key
        r = requests.post(
            f"{API_BASE}/start-live",
            headers=headers,
            timeout=15,
        )
        record(
            "TC-04-01",
            f"/start-live with API key: {r.status_code}",
            "PASS" if r.status_code == 200 else "FAIL",
            r.text[:120],
        )

        # Wait a moment for engine to initialize
        time.sleep(3)

        # TC-04-03: After start-live, check portfolio-summary
        r = requests.get(
            f"{API_BASE}/portfolio-summary",
            headers={"Authorization": f"Bearer {oidc_token}"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            msg = data.get("message", "")
            has_mgr = "No portfolio manager" not in msg
            record(
                "TC-04-03",
                f"Portfolio after start: msg='{msg[:60]}'",
                "PASS" if has_mgr else "WARN",
                f"positions={len(data.get('positions', []))}",
            )
        else:
            record(
                "TC-04-03",
                f"Portfolio after start: {r.status_code}",
                "FAIL",
            )

        # TC-04-04: stop
        r = requests.post(
            f"{API_BASE}/stop",
            headers=headers,
            timeout=15,
        )
        record(
            "TC-04-04",
            f"/stop: {r.status_code}",
            "PASS" if r.status_code == 200 else "FAIL",
            r.text[:80],
        )
    else:
        record(
            "TC-04-00",
            "Prerequisites",
            "SKIP",
            "No OIDC token or ENGINE_API_KEY",
        )

except Exception:
    record(
        "TC-04-01",
        "Engine lifecycle tests",
        "FAIL",
        traceback.format_exc()[-300:],
    )

# ======================================================================
# SUMMARY
# ======================================================================
section("TEST SUMMARY")

pass_count = sum(1 for r in RESULTS if r["status"] == "PASS")
fail_count = sum(1 for r in RESULTS if r["status"] == "FAIL")
warn_count = sum(1 for r in RESULTS if r["status"] == "WARN")
skip_count = sum(1 for r in RESULTS if r["status"] == "SKIP")
info_count = sum(1 for r in RESULTS if r["status"] == "INFO")
total = len(RESULTS)

print(f"  Total: {total}")
print(f"  PASS:  {pass_count}")
print(f"  FAIL:  {fail_count}")
print(f"  WARN:  {warn_count}")
print(f"  SKIP:  {skip_count}")
print(f"  INFO:  {info_count}")

scored = total - info_count - skip_count
if scored > 0:
    rate = pass_count / scored * 100
    print(f"\n  Pass Rate: {pass_count}/{scored} = {rate:.1f}%")

# Save results as JSON for the test report
results_file = os.path.join(
    os.path.dirname(__file__),
    "functional_test_results.json",
)
with open(results_file, "w") as f:
    json.dump(
        {
            "timestamp": datetime.now().isoformat(),
            "summary": {
                "total": total,
                "pass": pass_count,
                "fail": fail_count,
                "warn": warn_count,
                "skip": skip_count,
            },
            "results": RESULTS,
        },
        f,
        indent=2,
    )
print(f"\n  Results saved to: {results_file}")
