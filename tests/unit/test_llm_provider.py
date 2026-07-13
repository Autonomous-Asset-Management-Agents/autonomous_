"""G4a-1 (#1050): the LLM provider seam — core/llm/provider.py.

Contract under test (plan Rev 3, auditor-approved):
  * LLM_PROVIDER unset/"gemini"  → get_llm_provider() returns THE SAME object
    get_gemini_instance() returns (identity — byte-identical cloud default).
  * unknown value                → WARNING + gemini path (never raises).
  * "ollama"                     → OllamaProvider, but RuntimeError under
    K_SERVICE (BORA: no local LLM runtime on Cloud Run — fail-closed,
    precedent core/database/session.py _guard_cloud_sqlite_fallback).
  * OllamaProvider: deterministic decoding (temp 0.0, seed 7), 120s timeout,
    returns "" + WARNING on ANY error (same shape as a Gemini failure →
    graceful degrade), per-call AsyncClient (no event-loop affinity).
  * The seam adds NO caching on the gemini path (never memoize a None — a
    transient first-call failure must not kill the LLM for the process life).

NOTE (GKE runner): the self-hosted runner inherits K_SERVICE from the node
(tests/conftest.py precedent) — every ollama-path test strips it explicitly.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _no_kservice_env(extra=None):
    """patch.dict env without K_SERVICE (GKE runner inherits it from the node)."""
    env = {k: v for k, v in os.environ.items() if k != "K_SERVICE"}
    env.update(extra or {})
    return patch.dict(os.environ, env, clear=True)


class FactoryDefaultPath(unittest.TestCase):
    def setUp(self):
        from core.llm import provider as p

        p.reset_llm_provider()
        self.p = p

    def test_unset_returns_gemini_singleton_identity(self):
        sentinel = object()
        with _no_kservice_env(), patch(
            "core.gemini_client.get_gemini_instance", return_value=sentinel
        ):
            os.environ.pop("LLM_PROVIDER", None)
            self.assertIs(self.p.get_llm_provider(), sentinel)

    def test_explicit_gemini_same_identity(self):
        sentinel = object()
        with _no_kservice_env({"LLM_PROVIDER": "gemini"}), patch(
            "core.gemini_client.get_gemini_instance", return_value=sentinel
        ):
            self.assertIs(self.p.get_llm_provider(), sentinel)

    def test_unknown_value_warns_and_falls_back_to_gemini(self):
        # "anthropic"/"openai" are now first-class (P2); use a truly unknown value.
        sentinel = object()
        with _no_kservice_env({"LLM_PROVIDER": "mistral"}), patch(
            "core.gemini_client.get_gemini_instance", return_value=sentinel
        ), self.assertLogs(level="WARNING") as logs:
            self.assertIs(self.p.get_llm_provider(), sentinel)
        self.assertTrue(any("mistral" in m for m in logs.output))

    def test_gemini_none_is_never_cached_by_the_seam(self):
        # get_gemini_instance retries while None (gemini_client.py L188-196);
        # the seam must preserve that: every call delegates again.
        mock = MagicMock(side_effect=[None, None, object()])
        with _no_kservice_env(), patch("core.gemini_client.get_gemini_instance", mock):
            os.environ.pop("LLM_PROVIDER", None)
            self.assertIsNone(self.p.get_llm_provider())
            self.assertIsNone(self.p.get_llm_provider())
            self.assertIsNotNone(self.p.get_llm_provider())
        self.assertEqual(mock.call_count, 3)


class FactoryOllamaPath(unittest.TestCase):
    def setUp(self):
        from core.llm import provider as p

        p.reset_llm_provider()
        self.p = p

    def test_ollama_returns_ollama_provider_singleton(self):
        with _no_kservice_env({"LLM_PROVIDER": "ollama"}):
            first = self.p.get_llm_provider()
            second = self.p.get_llm_provider()
        self.assertIsInstance(first, self.p.OllamaProvider)
        self.assertIs(first, second)

    def test_reset_clears_singleton(self):
        with _no_kservice_env({"LLM_PROVIDER": "ollama"}):
            first = self.p.get_llm_provider()
            self.p.reset_llm_provider()
            second = self.p.get_llm_provider()
        self.assertIsNot(first, second)

    def test_concurrent_resolution_yields_one_instance(self):
        """PR_REVIEW_PROMPT.md §2.8 (Concurrency-Test-Mandat): the
        double-checked lock must hand EVERY concurrent caller the same
        OllamaProvider — never two instances from a check-then-act race."""
        from concurrent.futures import ThreadPoolExecutor

        with _no_kservice_env({"LLM_PROVIDER": "ollama"}):
            self.p.reset_llm_provider()
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(
                    pool.map(lambda _: self.p.get_llm_provider(), range(200))
                )
        self.assertEqual(
            len({id(r) for r in results}),
            1,
            "concurrent get_llm_provider() returned more than one instance",
        )
        self.assertIsInstance(results[0], self.p.OllamaProvider)

    def test_concurrent_get_and_reset_never_corrupts(self):
        """§2.8 hammer: interleaved get/reset across threads must never raise
        and must always yield a well-typed provider (a reset between the two
        null-checks of the lock is the classic corruption window)."""
        from concurrent.futures import ThreadPoolExecutor

        errors: list[Exception] = []

        def getter():
            try:
                for _ in range(300):
                    prov = self.p.get_llm_provider()
                    assert isinstance(prov, self.p.OllamaProvider)
            except Exception as exc:
                errors.append(exc)

        def resetter():
            try:
                for _ in range(300):
                    self.p.reset_llm_provider()
            except Exception as exc:
                errors.append(exc)

        with _no_kservice_env({"LLM_PROVIDER": "ollama"}):
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(getter) for _ in range(4)]
                futures += [pool.submit(resetter) for _ in range(4)]
                for f in futures:
                    f.result()
        self.assertEqual(errors, [], f"concurrent get/reset raised: {errors[:3]}")

    def test_kservice_guard_fails_closed(self):
        # BORA: LLM_PROVIDER=ollama on Cloud Run must be a LOUD misconfiguration.
        with _no_kservice_env({"LLM_PROVIDER": "ollama", "K_SERVICE": "engine"}):
            with self.assertRaises(RuntimeError):
                self.p.get_llm_provider()

    def test_no_kservice_no_raise(self):
        with _no_kservice_env({"LLM_PROVIDER": "ollama"}):
            self.assertIsNotNone(self.p.get_llm_provider())

    def test_env_config_respected(self):
        with _no_kservice_env(
            {
                "LLM_PROVIDER": "ollama",
                "OLLAMA_BASE_URL": "http://127.0.0.1:9999/",
                "LOCAL_LLM_MODEL": "mistral",
            }
        ):
            prov = self.p.get_llm_provider()
        self.assertEqual(prov.base_url, "http://127.0.0.1:9999")  # rstrip("/")
        self.assertEqual(prov.model, "mistral")


class OllamaGenerateSync(unittest.TestCase):
    def _provider(self):
        from core.llm.provider import OllamaProvider

        return OllamaProvider(model="llama3.2", base_url="http://localhost:11434")

    def test_happy_path_posts_deterministic_payload(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"response": "Sentiment: 0.7"}
        with patch("httpx.post", return_value=resp) as post:
            out = self._provider().generate_content("hello", max_output_tokens=128)
        self.assertEqual(out, "Sentiment: 0.7")
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/api/generate")
        payload = kwargs["json"]
        # Deterministic decoding — same input ⇒ same thesis (bundle-proven fix
        # for "insights re-roll on every restart").
        self.assertEqual(payload["options"]["temperature"], 0.0)
        self.assertEqual(payload["options"]["seed"], 7)
        self.assertEqual(payload["options"]["num_predict"], 128)
        self.assertFalse(payload["stream"])
        self.assertEqual(kwargs["timeout"], 120.0)

    def test_connection_error_returns_empty_and_warns(self):
        with patch("httpx.post", side_effect=OSError("refused")), self.assertLogs(
            level="WARNING"
        ):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_non_200_returns_empty_and_warns(self):
        resp = MagicMock(status_code=500)
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_malformed_json_returns_empty_and_warns(self):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("not json")
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_missing_response_key_returns_empty_string(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        with patch("httpx.post", return_value=resp):
            self.assertEqual(self._provider().generate_content("x"), "")


class OllamaGenerateAsync(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        from core.llm.provider import OllamaProvider

        return OllamaProvider(model="llama3.2", base_url="http://localhost:11434")

    def _client_cm(self, client):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_async_happy_path_uses_per_call_client(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"response": "ok"}
        client = MagicMock()
        client.post = AsyncMock(return_value=resp)  # §12.5 AsyncMock
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ) as client_cls:
            out1 = await self._provider().generate_content_async("a")
            out2 = await self._provider().generate_content_async("b")
        self.assertEqual((out1, out2), ("ok", "ok"))
        # Per-call client: a cached AsyncClient would bind to the first event
        # loop — exactly the bug class the per-loop-lock fix in #1166 addressed.
        self.assertEqual(client_cls.call_count, 2)

    async def test_async_error_returns_empty_and_warns(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=OSError("refused"))
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ), self.assertLogs(level="WARNING"):
            self.assertEqual(await self._provider().generate_content_async("x"), "")


class FactoryCloudProviderPath(unittest.TestCase):
    """P2 (#1406): the cloud providers (OpenAI / Anthropic) route to their own
    provider classes and — unlike ollama — are ALLOWED under K_SERVICE (they are
    cloud-native, not a local LLM runtime), so no _guard_cloud_ollama applies."""

    def setUp(self):
        from core.llm import provider as p

        p.reset_llm_provider()
        self.p = p

    def test_openai_returns_openai_provider_singleton(self):
        with _no_kservice_env({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-x"}):
            first = self.p.get_llm_provider()
            second = self.p.get_llm_provider()
        self.assertIsInstance(first, self.p.OpenAIProvider)
        self.assertIs(first, second)

    def test_anthropic_returns_anthropic_provider_singleton(self):
        with _no_kservice_env(
            {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant"}
        ):
            first = self.p.get_llm_provider()
            second = self.p.get_llm_provider()
        self.assertIsInstance(first, self.p.AnthropicProvider)
        self.assertIs(first, second)

    def test_openai_allowed_under_kservice(self):
        # Cloud provider on Cloud Run is legitimate — must NOT fail closed.
        with _no_kservice_env(
            {"LLM_PROVIDER": "openai", "K_SERVICE": "engine", "OPENAI_API_KEY": "sk-x"}
        ):
            self.assertIsInstance(self.p.get_llm_provider(), self.p.OpenAIProvider)

    def test_anthropic_allowed_under_kservice(self):
        with _no_kservice_env(
            {
                "LLM_PROVIDER": "anthropic",
                "K_SERVICE": "engine",
                "ANTHROPIC_API_KEY": "sk-ant",
            }
        ):
            self.assertIsInstance(self.p.get_llm_provider(), self.p.AnthropicProvider)

    def test_env_config_respected(self):
        with _no_kservice_env(
            {
                "LLM_PROVIDER": "openai",
                "OPENAI_API_KEY": "sk-x",
                "OPENAI_MODEL": "gpt-4o",
            }
        ):
            oai = self.p.get_llm_provider()
        with _no_kservice_env(
            {
                "LLM_PROVIDER": "anthropic",
                "ANTHROPIC_API_KEY": "sk-ant",
                "ANTHROPIC_MODEL": "claude-3-5-sonnet-latest",
            }
        ):
            ant = self.p.get_llm_provider()
        self.assertEqual(oai.model, "gpt-4o")
        self.assertEqual(ant.model, "claude-3-5-sonnet-latest")

    def test_defaults(self):
        from core.llm.provider import AnthropicProvider, OpenAIProvider

        self.assertEqual(OpenAIProvider(api_key="sk-x").model, "gpt-4o-mini")
        self.assertEqual(
            AnthropicProvider(api_key="sk-ant").model, "claude-3-5-haiku-latest"
        )


class OpenAIGenerateSync(unittest.TestCase):
    def _provider(self):
        from core.llm.provider import OpenAIProvider

        return OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")

    def test_happy_path_posts_deterministic_payload(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "choices": [{"message": {"content": "Sentiment: 0.7"}}]
        }
        with patch("httpx.post", return_value=resp) as post:
            out = self._provider().generate_content("hello", max_output_tokens=128)
        self.assertEqual(out, "Sentiment: 0.7")
        # Correct endpoint + bearer auth shape.
        self.assertEqual(
            post.call_args.args[0], "https://api.openai.com/v1/chat/completions"
        )
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sk-test")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "gpt-4o-mini")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_tokens"], 128)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(kwargs["timeout"], 60.0)

    def test_missing_api_key_returns_empty_and_warns(self):
        from core.llm.provider import OpenAIProvider

        with patch.dict(os.environ, {}, clear=True):
            prov = OpenAIProvider()  # no key anywhere
        with self.assertLogs(level="WARNING"):
            self.assertEqual(prov.generate_content("x"), "")

    def test_connection_error_returns_empty_and_warns(self):
        with patch("httpx.post", side_effect=OSError("refused")), self.assertLogs(
            level="WARNING"
        ):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_non_200_returns_empty_and_warns(self):
        resp = MagicMock(status_code=401)
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_malformed_json_returns_empty_and_warns(self):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = ValueError("not json")
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_missing_content_returns_empty_string(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {}
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")


class OpenAIGenerateAsync(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        from core.llm.provider import OpenAIProvider

        return OpenAIProvider(model="gpt-4o-mini", api_key="sk-test")

    def _client_cm(self, client):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_async_happy_path_uses_per_call_client(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
        client = MagicMock()
        client.post = AsyncMock(return_value=resp)  # §12.5 AsyncMock
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ) as client_cls:
            out1 = await self._provider().generate_content_async("a")
            out2 = await self._provider().generate_content_async("b")
        self.assertEqual((out1, out2), ("ok", "ok"))
        self.assertEqual(client_cls.call_count, 2)  # per-call client, no loop affinity
        self.assertEqual(
            client.post.call_args.args[0],
            "https://api.openai.com/v1/chat/completions",
        )

    async def test_async_missing_key_returns_empty_and_warns(self):
        from core.llm.provider import OpenAIProvider

        with patch.dict(os.environ, {}, clear=True):
            prov = OpenAIProvider()
        with self.assertLogs(level="WARNING"):
            self.assertEqual(await prov.generate_content_async("x"), "")

    async def test_async_error_returns_empty_and_warns(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=OSError("refused"))
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ), self.assertLogs(level="WARNING"):
            self.assertEqual(await self._provider().generate_content_async("x"), "")


class AnthropicGenerateSync(unittest.TestCase):
    def _provider(self):
        from core.llm.provider import AnthropicProvider

        return AnthropicProvider(model="claude-3-5-haiku-latest", api_key="sk-ant")

    def test_happy_path_posts_deterministic_payload(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"content": [{"type": "text", "text": "Thesis: buy"}]}
        with patch("httpx.post", return_value=resp) as post:
            out = self._provider().generate_content("hello", max_output_tokens=256)
        self.assertEqual(out, "Thesis: buy")
        self.assertEqual(
            post.call_args.args[0], "https://api.anthropic.com/v1/messages"
        )
        kwargs = post.call_args.kwargs
        # x-api-key + pinned anthropic-version header shape.
        self.assertEqual(kwargs["headers"]["x-api-key"], "sk-ant")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")
        payload = kwargs["json"]
        self.assertEqual(payload["model"], "claude-3-5-haiku-latest")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hello"}])
        self.assertEqual(kwargs["timeout"], 60.0)

    def test_missing_api_key_returns_empty_and_warns(self):
        from core.llm.provider import AnthropicProvider

        with patch.dict(os.environ, {}, clear=True):
            prov = AnthropicProvider()
        with self.assertLogs(level="WARNING"):
            self.assertEqual(prov.generate_content("x"), "")

    def test_connection_error_returns_empty_and_warns(self):
        with patch("httpx.post", side_effect=OSError("refused")), self.assertLogs(
            level="WARNING"
        ):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_non_200_returns_empty_and_warns(self):
        resp = MagicMock(status_code=529)
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")

    def test_missing_content_returns_empty_string(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"content": []}
        with patch("httpx.post", return_value=resp), self.assertLogs(level="WARNING"):
            self.assertEqual(self._provider().generate_content("x"), "")


class AnthropicGenerateAsync(unittest.IsolatedAsyncioTestCase):
    def _provider(self):
        from core.llm.provider import AnthropicProvider

        return AnthropicProvider(model="claude-3-5-haiku-latest", api_key="sk-ant")

    def _client_cm(self, client):
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)
        return cm

    async def test_async_happy_path_uses_per_call_client(self):
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"content": [{"type": "text", "text": "ok"}]}
        client = MagicMock()
        client.post = AsyncMock(return_value=resp)  # §12.5 AsyncMock
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ) as client_cls:
            out1 = await self._provider().generate_content_async("a")
            out2 = await self._provider().generate_content_async("b")
        self.assertEqual((out1, out2), ("ok", "ok"))
        self.assertEqual(client_cls.call_count, 2)
        self.assertEqual(
            client.post.call_args.args[0], "https://api.anthropic.com/v1/messages"
        )

    async def test_async_missing_key_returns_empty_and_warns(self):
        from core.llm.provider import AnthropicProvider

        with patch.dict(os.environ, {}, clear=True):
            prov = AnthropicProvider()
        with self.assertLogs(level="WARNING"):
            self.assertEqual(await prov.generate_content_async("x"), "")

    async def test_async_error_returns_empty_and_warns(self):
        client = MagicMock()
        client.post = AsyncMock(side_effect=OSError("refused"))
        with patch(
            "httpx.AsyncClient", return_value=self._client_cm(client)
        ), self.assertLogs(level="WARNING"):
            self.assertEqual(await self._provider().generate_content_async("x"), "")


class SeamConsumerGate(unittest.TestCase):
    """G4a-1 shipped the seam DORMANT; G4a-2 wires the consumers and G4a-2c adds
    the specialist. This gate pins "exactly the planned consumers import it" —
    so a stray import, or a forgotten migration, is caught at CI."""

    _MIGRATED = {
        "round_table/agents.py",
        "learning/engine.py",
        "market_scanner.py",
        "news_processor.py",
        "gemini_client.py",
        "stock_specialist.py",  # G4a-2c
        "xai/agent_core.py",
        "xai/intent_router.py",
    }

    def test_only_planned_consumers_import_the_seam(self):
        importers = set()
        for py in (_ROOT / "core").rglob("*.py"):
            if py.parent.name == "llm":
                continue
            text = py.read_text(encoding="utf-8", errors="ignore")
            if "get_llm_provider" in text or "core.llm.provider" in text:
                importers.add(py.relative_to(_ROOT / "core").as_posix())
        missing = self._MIGRATED - importers
        self.assertEqual(
            missing, set(), f"planned G4a-2 consumers not wired to the seam: {missing}"
        )
        extra = importers - self._MIGRATED
        self.assertEqual(
            extra,
            set(),
            f"unexpected seam importer(s) — migrate via a reviewed PR (e.g. "
            f"stock_specialist = G4a-2c): {extra}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
