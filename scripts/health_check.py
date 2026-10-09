"""Quick health check for aaa-backend on Cloud Run."""

import os
import subprocess
import sys
import urllib.request

os.environ["PYTHONIOENCODING"] = "utf-8"
sys.stdout.reconfigure(encoding="utf-8")

SERVICE_URL = "https://aaa-backend-376527821918.europe-west3.run.app"

# Get OIDC token
token = (
    subprocess.check_output(["gcloud", "auth", "print-identity-token"])  # noqa: S603
    .decode()
    .strip()
)

endpoints = ["/health", "/strategy", "/compliance-status"]

for ep in endpoints:
    url = f"{SERVICE_URL}{ep}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        resp = urllib.request.urlopen(req, timeout=10)  # nosec B310 — https only
        data = resp.read().decode()[:300]
        print(f"✅ {ep} → {resp.status}: {data}")
    except urllib.error.HTTPError as e:
        print(f"❌ {ep} → {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"❌ {ep} → ERROR: {e}")

# Auth guard test: POST /stop without API key → expect 403
print("\n--- Auth Guard Test ---")
req = urllib.request.Request(
    f"{SERVICE_URL}/stop", method="POST", headers={"Authorization": f"Bearer {token}"}
)
try:
    resp = urllib.request.urlopen(req, timeout=10)  # nosec B310 — https only
    print(f"⚠️ /stop without key → {resp.status} (expected 403!)")
except urllib.error.HTTPError as e:
    if e.code == 403:
        print("✅ /stop without key → 403 (auth guard working)")
    else:
        print(f"❌ /stop without key → {e.code}: {e.read().decode()[:200]}")
