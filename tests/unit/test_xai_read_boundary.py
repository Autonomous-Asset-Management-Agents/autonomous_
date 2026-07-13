# tests/unit/test_xai_read_boundary.py
# XAI-1 / XAI-T8 (#1337) — Unified Read-Only-Auth-Boundary.
# Pins the 4 Gherkin guarantees + the adversarial fail-closed edges surfaced in the pre-PR
# review: read-only ALLOW-LIST of verbs (camelCase/synonym writes denied), RLS scope
# (falsy-but-present id still scopes; Enterprise drops untagged; mismatched dropped),
# external allow-list (userinfo + scheme + malformed-IPv6 + non-str -> deny, never crash;
# trailing-dot FQDN equivalent; guard_source returns a canonical URL), flag, import-light.
import os
import subprocess
import sys

import allure
import pytest

from core.xai.read_boundary import (
    ReadAccessDenied,
    XaiReadBoundary,
    guard_read_only,
    is_read_boundary_enabled,
    is_read_only,
    is_source_allowed,
    load_allowlist,
    scope_rows,
    source_host,
)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestReadOnly:
    @pytest.mark.parametrize(
        "op",
        [
            "read_decisions",
            "search",
            "get_report",
            "get_feature_importance",
            "list_faq",
            "fetch_quote",
            "find_entry",
            "count_rows",
            "describe_schema",
            "load_report",
            "has_data",
            "is_ready",
            "view_history",
        ],
    )
    def test_reads_allowed(self, op):
        assert is_read_only(op) is True
        guard_read_only(op)  # must not raise

    @pytest.mark.parametrize(
        "op",
        # exact verbs, snake_case, camelCase, synonyms NOT in a naive deny-list, empty,
        # and non-str — every one must be denied (fail-closed allow-list of read verbs).
        [
            "write",
            "update",
            "delete",
            "insert",
            "upsert",
            "remove",
            "create",
            "drop",
            "save",
            "commit",
            "execute",
            "set_status",
            "put_row",
            "post_order",
            "patch_entry",
            "writeRow",
            "deleteAll",
            "updateMany",
            "merge",
            "replace",
            "rename",
            "sync",
            "flush",
            "mutate",
            "overwrite",
            "EXECUTE_SQL",
            "",
            "compute_hash",
            123,
            None,
        ],
    )
    def test_writes_and_unknown_denied(self, op):
        assert is_read_only(op) is False
        with pytest.raises(ReadAccessDenied):
            guard_read_only(op)


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestRlsScope:
    ROWS = [
        {"session_user_id": "A", "x": 1},
        {"session_user_id": "B", "x": 2},
        {"x": 3},  # untagged
    ]

    def test_user_sees_only_own_and_untagged(self):
        assert [r["x"] for r in scope_rows(self.ROWS, "A")] == [1, 3]

    def test_falsy_but_present_id_still_scopes(self):
        # P1 fix: session_user_id=0 / "" must NOT disable scoping (only None opts out).
        rows = [
            {"session_user_id": 0, "x": 1},
            {"session_user_id": "B", "x": 2},
            {"x": 3},
        ]
        assert [r["x"] for r in scope_rows(rows, 0)] == [1, 3]  # not [1,2,3]
        assert [r["x"] for r in scope_rows(rows, "")] == [
            3
        ]  # only untagged + (none == "")

    def test_none_id_passes_all(self):
        assert [r["x"] for r in scope_rows(self.ROWS, None)] == [1, 2, 3]

    def test_enterprise_drops_untagged(self):
        # allow_untagged=False (multi-tenant): an untagged/NULL-user row never bleeds.
        assert [r["x"] for r in scope_rows(self.ROWS, "A", allow_untagged=False)] == [1]

    def test_non_dicts_dropped(self):
        assert scope_rows([1, "j", None, {"session_user_id": "A"}], "A") == [
            {"session_user_id": "A"}
        ]


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestAllowListSecurity:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ("https://api.example.com/v1/quote", "api.example.com"),
            ("api.example.com", "api.example.com"),
            ("api.example.com:8443", "api.example.com"),
            ("HTTPS://API.Example.COM/x", "api.example.com"),
            ("https://api.example.com./x", "api.example.com"),  # trailing FQDN dot
            ("", None),
            ("   ", None),
            ("https://", None),
        ],
    )
    def test_source_host(self, source, expected):
        assert source_host(source) == expected

    @pytest.mark.parametrize(
        "bad", [123, None, b"https://api.example.com", ["x"], {"a": 1}]
    )
    def test_non_str_source_denied_not_crash(self, bad):
        assert source_host(bad) is None
        assert is_source_allowed(bad, {"api.example.com"}) is False

    @pytest.mark.parametrize(
        "source",
        ["http://[::1", "http://[not-ipv6]", "https://exa mple.com"],
    )
    def test_malformed_does_not_crash(self, source):
        # urlparse raises on a malformed IPv6 URL — the guard must DENY, never propagate.
        assert is_source_allowed(source, {"api.example.com"}) in (True, False)
        source_host(source)  # must not raise

    def test_userinfo_trick_denied(self):
        al = {"allowed.com"}
        assert is_source_allowed("https://allowed.com@evil.com", al) is False
        assert is_source_allowed("https://evil.com@allowed.com", al) is False

    def test_fragment_does_not_smuggle_host(self):
        # '#@evil.com' is a fragment -> host is allowed.com (fragment dropped) -> allowed.
        assert (
            is_source_allowed("https://allowed.com#@evil.com", {"allowed.com"}) is True
        )

    @pytest.mark.parametrize(
        "source",
        [
            "file:///etc/passwd",
            "data:text/html,x",
            "javascript:alert(1)",
            "ftp://allowed.com/x",
        ],
    )
    def test_non_http_scheme_denied(self, source):
        assert (
            is_source_allowed(source, {"allowed.com", "etc", "data", "javascript"})
            is False
        )

    def test_ip_only_if_listed(self):
        assert is_source_allowed("http://127.0.0.1/x", {"api.example.com"}) is False
        assert is_source_allowed("http://127.0.0.1/x", {"127.0.0.1"}) is True

    def test_subdomain_not_matched(self):
        assert (
            is_source_allowed("https://evil.api.example.com", {"api.example.com"})
            is False
        )

    def test_empty_allowlist_denies(self):
        assert is_source_allowed("https://api.example.com", set()) is False

    def test_trailing_dot_equivalent(self):
        assert (
            is_source_allowed("https://api.example.com./x", {"api.example.com"}) is True
        )
        assert (
            is_source_allowed("https://api.example.com/x", {"api.example.com."}) is True
        )

    def test_load_allowlist_env(self, monkeypatch):
        monkeypatch.setenv("XAI_EXTERNAL_ALLOWLIST", " A.com. , b.com ,, ")
        assert load_allowlist() == frozenset({"a.com", "b.com"})

    def test_load_allowlist_empty_default(self, monkeypatch):
        monkeypatch.delenv("XAI_EXTERNAL_ALLOWLIST", raising=False)
        assert load_allowlist() == frozenset()


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestXaiReadBoundary:
    def test_guard_source_returns_canonical_url(self):
        b = XaiReadBoundary(allowlist=["api.example.com"])
        assert (
            b.guard_source("https://api.example.com/v1/quote?x=1")
            == "https://api.example.com/v1/quote?x=1"
        )
        # fragment/userinfo smuggle stripped -> the caller fetches the SAFE canonical url
        assert (
            b.guard_source("https://api.example.com#@evil.com")
            == "https://api.example.com"
        )

    def test_guard_source_denies_non_listed(self):
        b = XaiReadBoundary(allowlist=["api.example.com"])
        with pytest.raises(ReadAccessDenied):
            b.guard_source("https://evil.com")
        with pytest.raises(ReadAccessDenied):
            b.guard_source("https://api.example.com@evil.com")  # userinfo -> denied

    def test_scope_uses_session_user_id(self):
        b = XaiReadBoundary(session_user_id="A")
        rows = [{"session_user_id": "A", "x": 1}, {"session_user_id": "B", "x": 2}]
        assert [r["x"] for r in b.scope(rows)] == [1]

    def test_enterprise_boundary_drops_untagged(self):
        b = XaiReadBoundary(session_user_id="A", allow_untagged=False)
        assert b.scope([{"session_user_id": "A", "x": 1}, {"x": 2}]) == [
            {"session_user_id": "A", "x": 1}
        ]

    def test_guard_read_only(self):
        b = XaiReadBoundary()
        b.guard_read_only("read_decisions")
        with pytest.raises(ReadAccessDenied):
            b.guard_read_only("deleteAll")

    def test_allowlist_from_env_when_unset(self, monkeypatch):
        monkeypatch.setenv("XAI_EXTERNAL_ALLOWLIST", "feed.example.com")
        b = XaiReadBoundary()
        assert b.source_allowed("https://feed.example.com/news") is True
        assert b.source_allowed("https://api.example.com") is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestFlag:
    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on", "  On  "])
    def test_enabled_truthy(self, val, monkeypatch):
        monkeypatch.setenv("XAI_RLS_BOUNDARY", val)
        assert is_read_boundary_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "", "off", "no", "nope"])
    def test_disabled_otherwise(self, val, monkeypatch):
        monkeypatch.setenv("XAI_RLS_BOUNDARY", val)
        assert is_read_boundary_enabled() is False

    def test_off_by_default(self, monkeypatch):
        monkeypatch.delenv("XAI_RLS_BOUNDARY", raising=False)
        assert is_read_boundary_enabled() is False


@allure.feature("XAI-1 Transparency Window")
@allure.story("Unified Read-Only-Auth-Boundary (XAI-T8)")
class TestImportLight:
    def test_no_torch_pulled(self):
        root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        code = (
            "import sys\n"
            "import core.xai.read_boundary\n"
            "bad = sorted(m for m in sys.modules if m == 'torch' or m.startswith('torch.'))\n"
            "assert not bad, bad\n"
        )
        r = subprocess.run(
            [sys.executable, "-c", code], cwd=root, capture_output=True, text=True
        )
        assert r.returncode == 0, (r.stdout, r.stderr)
