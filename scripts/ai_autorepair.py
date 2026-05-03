import os
import requests
import json
import re
from google import genai
from google.genai import types


def get_job_log(job_url, headers):
    """Fetches the log for a specific job. Requires a redirect follow."""
    log_url = job_url + "/logs"
    resp = requests.get(log_url, headers=headers, allow_redirects=True, timeout=30)
    if resp.status_code == 200:
        return resp.text
    else:
        print(f"Failed to fetch logs from {log_url}: {resp.status_code}")
        return ""


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    github_token = os.environ.get("GITHUB_TOKEN")
    run_id = os.environ.get("WORKFLOW_RUN_ID")
    repo_owner = os.environ.get("REPO_OWNER")
    repo_name = os.environ.get("REPO_NAME")
    workspace = os.environ.get("WORKSPACE_DIR", ".")

    if not all([api_key, github_token, run_id, repo_owner, repo_name]):
        print("Missing required environment variables. Aborting.")
        return

    print(f"Starting AI Auto-Repair for Run ID: {run_id}")

    # 1. Fetch failing jobs logs
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    jobs_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/actions/runs/{run_id}/jobs"
    jobs_resp = requests.get(jobs_url, headers=headers, timeout=30)
    if jobs_resp.status_code != 200:
        print(f"Error fetching jobs: {jobs_resp.text}")
        return

    jobs_data = jobs_resp.json()

    failed_logs = ""
    for job in jobs_data.get("jobs", []):
        if job["conclusion"] == "failure":
            print(f"Fetching logs for failed job: {job['name']}")
            log_text = get_job_log(job["url"], headers)
            # Take the last 8000 characters to capture the actual traceback/lint error
            failed_logs += f"\n=== Job: {job['name']} ===\n"
            failed_logs += log_text[-8000:]

    if not failed_logs:
        print("No failed logs found or failed to download. Aborting.")
        return

    # Extract filenames that might be involved. Looks for .py, .yaml, .js, .ts
    file_candidates = set(
        re.findall(r"([a-zA-Z0-9_/\-\\]+\.(?:py|yml|yaml|js|ts|json|md))", failed_logs)
    )

    source_context = ""
    for pf in file_candidates:
        # Ignore common non-source paths found in logs
        if "node_modules" in pf or ".venv" in pf or "site-packages" in pf:
            continue

        filepath = os.path.join(workspace, pf)
        # Fallback to AI Trading Bot prefix if relative path in logs omitted it
        if not os.path.exists(filepath):
            alt_path = os.path.join(workspace, "AI Trading Bot", pf)
            if os.path.exists(alt_path):
                filepath = alt_path
            else:
                continue

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                source_context += f"\n--- File: {pf} ---\n{f.read()}"
        except Exception as e:
            print(f"Could not read {filepath}: {e}")

    if not source_context:
        print(
            "Could not identify any local source files associated with the error logs."
        )
        print("Logs analyzed:\n" + failed_logs[:500] + "...")
        return

    print("Requesting fix from Gemini AI...")
    prompt = f"""
You are an expert Python developer and CI repair agent autonomously fixing a Pull Request.
A GitHub Actions CI run just failed.

Here are the tail ends of the failed logs:
<logs>
{failed_logs}
</logs>

Here is the source code for the files potentially involved in the error:
<source>
{source_context}
</source>

Your task is to fix the errors. Analyze the logs, identify the bug (e.g., Flake8, syntax errors, failing Pytest), and provide the FULL, corrected file contents.
Output your response in the following format for each file that needs changing:

```python:filename
# Full file contents here
```

Replace `python:filename` with the actual filename including extension (e.g. `core/events.py` or `.github/workflows/ci.yml`).
Ensure the filename exactly matches the one provided in the <source> block (the part after "--- File: ").
DO NOT provide partial snippets; you must provide the ENTIRE file content so it can be overwritten safely.
"""

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.0),
    )

    content = response.text

    # Matches ```language:filename ... ```
    pattern = (
        r"```(?:python|yaml|javascript|typescript|json|markdown):([^\n]+)\n(.*?)\n```"
    )
    matches = list(re.finditer(pattern, content, re.DOTALL))

    files_changed = 0
    for match in matches:
        filename = match.group(1).strip()
        new_code = match.group(2)

        filepath = os.path.join(workspace, filename)
        if not os.path.exists(filepath):
            alt_path = os.path.join(workspace, "AI Trading Bot", filename)
            if os.path.exists(alt_path):
                filepath = alt_path

        if os.path.exists(filepath):
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(new_code)
            print(f"✅ Patched {filepath}")
            files_changed += 1
        else:
            print(
                f"⚠️ AI suggested a fix for {filename}, but the file could not be found locally."
            )

    if files_changed == 0:
        print("AI did not return any parseable code blocks. Here was the raw response:")
        print(content)
    else:
        print(f"Successfully repaired {files_changed} files.")


if __name__ == "__main__":
    main()
