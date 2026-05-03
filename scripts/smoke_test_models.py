#!/usr/bin/env python3
"""
Docker Smoke Test: Model Loading Verification
Prueft ob LSTM (v2) und RL (v5) Modelle aus GCS korrekt geladen werden.
"""
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

errors = []

# --- LSTM Model ---
try:
    import torch

    p = "data/lstm_model_v2.pth"
    if not os.path.exists(p):
        errors.append(f"MISSING FILE: {p}")
    else:
        try:
            s = torch.load(p, map_location="cpu", weights_only=True)
        except Exception as e1:
            logging.warning(
                f"weights_only=True failed ({e1}) - retrying with weights_only=False"
            )
            s = torch.load(p, map_location="cpu", weights_only=False)
        logging.info(f"SMOKE OK LSTM: {len(s)} keys loaded from {p}")
except Exception as e:
    errors.append(f"SMOKE FAIL LSTM: {e}")

# --- RL Model ---
try:
    p = "data/rl_agent_v5.zip"
    if not os.path.exists(p):
        errors.append(f"MISSING FILE: {p}")
    else:
        # Try RecurrentPPO first (LSTM policy), then DQN fallback
        try:
            from stable_baselines3.common.policies import ActorCriticPolicy
            from sb3_contrib import RecurrentPPO

            m = RecurrentPPO.load(p, device="cpu")
        except Exception:
            from stable_baselines3 import DQN

            m = DQN.load(p, device="cpu")
        obs_dim = m.observation_space.shape[0]
        logging.info(f"SMOKE OK RL: obs_dim={obs_dim} policy={type(m.policy).__name__}")
        if obs_dim != 12:
            errors.append(f"OBSERVATION SPACE MISMATCH: expected 12, got {obs_dim}")
        else:
            logging.info("SMOKE OK RL obs_dim=12 CONFIRMED")
except Exception as e:
    errors.append(f"SMOKE FAIL RL: {e}")

# --- Model files inventory ---
data_dir = "data"
if os.path.isdir(data_dir):
    files = os.listdir(data_dir)
    logging.info(f"Files in {data_dir}/: {sorted(files)}")
else:
    errors.append(f"data/ directory does not exist - GCS sync may have failed")

# --- Result ---
if errors:
    print("\n=== SMOKE TESTS FAILED ===")
    for e in errors:
        print(f"  ❌ {e}")
    sys.exit(1)
else:
    print("\n=== ALL SMOKE TESTS PASSED ===")
    sys.exit(0)
