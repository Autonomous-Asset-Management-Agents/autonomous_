"""#3628 — the public console API can be locked to a hardened READ-ONLY profile.

When PUBLIC_READ_ONLY is set (the public live-demo deployment), the read-only guard
refuses EVERY mutating request server-side — by a method whitelist (only
GET/HEAD/OPTIONS pass), so a newly-added POST/PUT/DELETE route cannot slip past.
Tested in isolation on a throwaway app so it needs none of serve_public_api's heavy
deps (redis / firebase / limiter); the guard is method-based, so refusing one POST
route proves it refuses all of them.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from public_readonly import read_only_guard

pytestmark = pytest.mark.vc0


@pytest.fixture
def client():
    app = FastAPI()
    app.middleware("http")(read_only_guard)

    @app.get("/read")
    def _read():
        return {"ok": True}

    @app.post("/write")
    def _write():
        return {"ok": True}  # pragma: no cover — must never be reached while read-only

    @app.put("/put")
    def _put():
        return {"ok": True}  # pragma: no cover

    @app.delete("/del")
    def _del():
        return {"ok": True}  # pragma: no cover

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(
    "method,path", [("post", "/write"), ("put", "/put"), ("delete", "/del")]
)
def test_read_only_refuses_every_mutation(client, monkeypatch, method, path):
    monkeypatch.setenv("PUBLIC_READ_ONLY", "true")
    r = getattr(client, method)(path)
    assert (
        r.status_code == 405
    ), f"{method.upper()} {path} not refused (got {r.status_code})"
    assert "read-only" in r.text.lower()
    assert r.headers.get("Allow") == "GET, HEAD, OPTIONS"


def test_read_only_still_serves_reads(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_READ_ONLY", "true")
    r = client.get("/read")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_writes_pass_through_when_not_read_only(client, monkeypatch):
    monkeypatch.delenv("PUBLIC_READ_ONLY", raising=False)
    r = client.post("/write")
    assert r.status_code == 200  # gate is a no-op when the flag is off


@pytest.mark.parametrize("val", ["false", "0", "no", "off", "", "  "])
def test_only_truthy_values_enable_the_gate(client, monkeypatch, val):
    monkeypatch.setenv("PUBLIC_READ_ONLY", val)
    assert client.post("/write").status_code == 200
