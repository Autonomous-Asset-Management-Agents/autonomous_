"""Serve the Web UI on port 8081. Uses static HTML/CSS/JS (no Node required)."""

import http.server
import os
import sys
import threading
import webbrowser


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.normpath(os.path.join(script_dir, ".."))
    # Prefer built React app (repo root), then static fallback
    dist_root = os.path.join(repo_root, "dist")
    static_root = os.path.join(repo_root, "static")

    if os.path.isdir(dist_root) and os.path.exists(
        os.path.join(dist_root, "index.html")
    ):
        serve_dir = dist_root
    elif os.path.isdir(static_root) and os.path.exists(
        os.path.join(static_root, "index.html")
    ):
        serve_dir = static_root
    else:
        print(
            "ERROR: No Web UI found. Run 'npm run build' in repo root, or add repo root static/ with index.html.",
            file=sys.stderr,
        )
        sys.exit(1)

    os.chdir(serve_dir)
    "dist (React build)" if "dist" in serve_dir else "static (fallback)"
    handler = http.server.SimpleHTTPRequestHandler
    for port in range(8081, 8090):
        try:
            httpd = http.server.HTTPServer(("", port), handler)
            break
        except OSError:
            if port == 8089:
                print(
                    "ERROR: No free port in 8081–8089. Close other apps using these ports.",
                    file=sys.stderr,
                )
                sys.exit(1)
            continue
    url = f"http://localhost:{port}"
    print(f"Web UI: {url}")
    if port != 8081:
        print(f"(Port 8081 in use; using {port})")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    httpd.serve_forever()


if __name__ == "__main__":
    main()
