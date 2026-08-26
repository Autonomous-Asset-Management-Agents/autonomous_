"""Delete the old unauthenticated uptime check and update the alert policy filter."""

import json
import subprocess
import urllib.error
import urllib.request

PROJECT = "aaagents-oss"
OLD_CHECK_ID = "aaa-backend-health-check-Y_J1rax-ND0"
NEW_CHECK_ID = "aaa-backend-health-oidc-uAaMGog3wn4"
ALERT_POLICY_ID = "11580287828195200347"

# Get access token
token = (
    subprocess.check_output(["gcloud.cmd", "auth", "print-access-token"], shell=False)
    .decode()
    .strip()
)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

# 1. Delete old uptime check
print(f"Deleting old uptime check: {OLD_CHECK_ID} ...")
# Must use the full resource name as the URL path segment
full_name = f"projects/{PROJECT}/uptimeCheckConfigs/{OLD_CHECK_ID}"
url = f"https://monitoring.googleapis.com/v3/{full_name}"
req = urllib.request.Request(url, method="DELETE", headers=headers)
try:
    resp = urllib.request.urlopen(req)  # nosec B310
    print(f"  OK Deleted (HTTP {resp.status})")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"  FAIL HTTP {e.code}: {body}".encode("ascii", errors="replace").decode())

# 2. Update alert policy to use new check ID
print(f"\nUpdating alert policy {ALERT_POLICY_ID} to new check ID ...")
get_url = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/alertPolicies/{ALERT_POLICY_ID}"
get_req = urllib.request.Request(get_url, headers=headers)
policy = json.loads(urllib.request.urlopen(get_req).read())  # nosec B310

# Fix check_id AND resource.type (new check uses cloud_run_revision, not uptime_url)
for condition in policy.get("conditions", []):
    ct = condition.get("conditionThreshold", {})
    old_filter = ct.get("filter", "")
    new_filter = old_filter.replace(OLD_CHECK_ID, NEW_CHECK_ID)
    new_filter = new_filter.replace(
        'resource.type="uptime_url"', 'resource.type="cloud_run_revision"'
    )
    ct["filter"] = new_filter
    print(f"  Old filter: {old_filter}")
    print(f"  New filter: {new_filter}")

# PATCH the policy
patch_url = f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/alertPolicies/{ALERT_POLICY_ID}?updateMask=conditions"
patch_data = json.dumps(policy).encode()
patch_req = urllib.request.Request(
    patch_url, data=patch_data, method="PATCH", headers=headers
)
try:
    resp = urllib.request.urlopen(patch_req)  # nosec B310
    print(f"  OK Alert policy updated (HTTP {resp.status})")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    print(f"  FAIL HTTP {e.code}: {body}".encode("ascii", errors="replace").decode())

print("\nDone.")
