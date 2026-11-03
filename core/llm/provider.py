"""LLM provider seam (G4a-1, #1050) — the single sanctioned LLM entry point.

The desktop edition lets the user choose a cloud LLM (API key) OR a fully
local Ollama; the cloud edition must never depend on a local LLM runtime
(BORA). One factory resolves the provider from ``LLM_PROVIDER``:

  unset / "gemini"   -> the existing ``get_gemini_instance()`` singleton,
                        returned UNWRAPPED and UNCACHED — the cloud default
                        path is the identical object it is today, and the
                        gemini client's retry-while-None contract
                        (core/gemini_client.py L188-196) stays intact.
  "ollama"           -> ``OllamaProvider`` (desktop opt-in), refused LOUDLY
                        under Cloud Run (K_SERVICE) — fail-closed, precedent
                        ``core/database/session.py`` `_guard_cloud_sqlite_fallback`.
  "openai"           -> ``OpenAIProvider`` (P2, #1406) — cloud-native, ALLOWED
                        under Cloud Run (no _guard_cloud_ollama); needs OPENAI_API_KEY.
  "anthropic"        -> ``AnthropicProvider`` (P2, #1406) — cloud-native, ALLOWED
                        under Cloud Run; needs ANTHROPIC_API_KEY.
  anything else      -> WARNING + gemini path.

This module is DORMANT in G4a-1: no production code imports it until the
consumer-migration PRs (G4a-2/2c) — enforced by the grep-gate test in
tests/unit/test_llm_provider.py.

Both providers expose the same duck-typed surface consumers already use:
``generate_content(prompt, max_output_tokens) -> str`` and
``generate_content_async(...)``.

#4939 (Epic #4638): the cloud providers additionally expose the OPT-IN
``generate_with_search_async(prompt, max_output_tokens) -> SuchAntwort`` (provider
web search; only the LLM prompt run uses it). The two methods above stay
byte-identical; ``get_llm_provider_fuer(anbieter)`` resolves a provider by name,
independent of ``LLM_PROVIDER``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.llm.health import resolved_model_name, resolved_provider_name
from core.llm.telemetry import record_call

logger = logging.getLogger(__name__)

_OLLAMA_TIMEOUT_S = 120.0  # CPU boxes generate slowly; cap, don't hang forever
_CLOUD_TIMEOUT_S = 60.0  # hosted APIs answer fast; cap so a hang can't wedge a run
# #4939: Deckel der Anthropic-Websuche je Anfrage (Doku: einfache Anfragen 1-3 Suchen).
# Vorschlag des Plans, Owner-Entscheidung offen (Plan §6); je Suche 10 $ / 1.000.
WEBSUCHE_MAX_USES = 3


@dataclass(frozen=True)
class SuchAntwort:
    """#4939: Antwort eines Aufrufs mit Websuche — Text plus die zitierten Quell-URLs."""

    text: str = ""
    quellen: List[str] = field(default_factory=list)


def _eindeutig(urls: Iterable[Any]) -> List[str]:
    """Quell-URLs in Antwortreihenfolge, ohne Leere und ohne Doppelte."""
    gesehen: Dict[str, None] = {}
    for url in urls:
        if isinstance(url, str) and url.strip():
            gesehen.setdefault(url.strip(), None)
    return list(gesehen)


async def _suche_async(
    name: str,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    extract: Callable[[Dict[str, Any]], Optional[SuchAntwort]],
) -> SuchAntwort:
    """#4939: ein Suchaufruf mit dem Fehlervertrag der Seam — nie werfen, leere
    ``SuchAntwort`` mit WARNING (CODING_POLICY §5.6). ``extract`` liefert None fuer eine
    unbrauchbare Antwort und loggt den Grund selbst."""
    _t0 = time.perf_counter()
    _obs_exc: Optional[BaseException] = None
    try:
        import httpx

        # Per-call client: same event-loop reasoning as generate_content_async.
        async with httpx.AsyncClient(timeout=_CLOUD_TIMEOUT_S) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code != 200:
            logger.warning(
                "%s: HTTP %s from web search — returning empty reply.",
                name,
                resp.status_code,
            )
            _obs_exc = RuntimeError(f"http_{resp.status_code}")
            return SuchAntwort()
        antwort = extract(resp.json())
        if antwort is None:
            _obs_exc = RuntimeError("unusable_search_reply")
            return SuchAntwort()
        return antwort
    except Exception as exc:
        logger.warning("%s: web search failed (%s) — returning empty reply.", name, exc)
        _obs_exc = exc
        return SuchAntwort()
    finally:
        record_call(_t0, _obs_exc)


def _guard_cloud_ollama() -> None:
    """BORA fail-closed: a local LLM runtime must never be a Cloud Run dependency."""
    from core.composition.root import CompositionRoot

    if CompositionRoot.get_instance().is_cloud:
        raise RuntimeError(
            "LLM_PROVIDER=ollama is forbidden on Cloud Run (BORA: cloud-native "
            "must not depend on a local LLM runtime). Configure GEMINI_API_KEY."
        )


class OllamaProvider:
    """Local-LLM provider backed by Ollama's /api/generate endpoint.

    Error contract: returns "" on ANY failure (WARNING-logged, never DEBUG —
    CODING_POLICY §5.6, never raises) so callers see the same shape as a
    failed Gemini call and degrade gracefully.

    Decoding is DETERMINISTIC (temperature 0.0, fixed seed) — same input ⇒
    same output, so desktop insights don't re-roll on every engine restart
    (bundle-proven).
    """

    def __init__(
        self, model: Optional[str] = None, base_url: Optional[str] = None
    ) -> None:
        self.base_url = (
            base_url or os.getenv("OLLAMA_BASE_URL") or "http://localhost:11434"
        ).rstrip("/")
        self.model = model or resolved_model_name(
            "ollama"
        )  # #2962: one source of truth

    def _payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # #2424: keep the model resident between engine cycles. Ollama's
            # default unloads it after ~5 min idle, so each timed cycle
            # cold-reloads the runner — on older Ollama builds that runner
            # restart flashes a console window. "30m" spans the slowest cycle;
            # OLLAMA_KEEP_ALIVE overrides ("-1" = never unload).
            #
            # #2706 — CORRECTION, this comment used to read "(news 5m,
            # round-table 30m)". Both halves were wrong and the error mattered:
            # it made "throttle the round table to cut LLM load" look sensible.
            #   * The round table makes NO LLM call at all. NewsSentimentAgent's
            #     LLM prior was dropped (round_table/agents.py) and its NLP path
            #     scores headlines with a LOCAL FinBERT, not through this seam.
            #   * The news poller makes none either on desktop: it returns
            #     before starting its thread without POLYGON_API_KEY
            #     (engine/news_poller.py), and Polygon is not configured there.
            # The real periodic caller is the market scanner —
            # engine/monitor_loop.py runs scan_market every
            # STRATEGY_MONITOR_INTERVAL_SECONDS (1800 = 30 min) and
            # market_scanner.py calls the provider. That 30-minute call lands on
            # the still-loaded model and RENEWS this 30-minute lease, so the
            # model never unloads. Two independently chosen values that happen
            # to coincide — which is why the model SIZE, not the cadence, is the
            # lever for a 16 GB machine (see docs/ram-aware-model-choice/).
            # Env-boundary read per the module's ADR-014 convention (see
            # OLLAMA_BASE_URL / LOCAL_LLM_MODEL above) — deliberately not a
            # get_config() field, to avoid config.oss.py drift for a desktop-only
            # value.
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE") or "30m",
            "options": {
                "temperature": 0.0,
                "seed": 7,
                "num_predict": max_output_tokens,
            },
        }

    def generate_content(self, prompt: str, max_output_tokens: int = 512) -> str:
        # ADR-OBS-01 / PR D: time the real generate call (PURE OBSERVATION). The
        # perf_counter + record_call are fail-safe — a timing failure can never
        # alter this method's result or its "" error contract.
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            import httpx

            resp = httpx.post(
                f"{self.base_url}/api/generate",
                json=self._payload(prompt, max_output_tokens),
                timeout=_OLLAMA_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.warning(
                    "OllamaProvider: HTTP %s from %s — returning empty reply.",
                    resp.status_code,
                    self.base_url,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return str(resp.json().get("response") or "")
        except Exception as exc:
            logger.warning(
                "OllamaProvider: generate failed (%s) — returning empty reply.", exc
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)

    async def generate_content_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> str:
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            import httpx

            # Per-call client: a cached AsyncClient binds to the first event
            # loop it runs on (same bug class as the per-loop lock in
            # core/database/session.py). Connection setup to localhost is
            # negligible next to generation time.
            async with httpx.AsyncClient(timeout=_OLLAMA_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self.base_url}/api/generate",
                    json=self._payload(prompt, max_output_tokens),
                )
            if resp.status_code != 200:
                logger.warning(
                    "OllamaProvider: HTTP %s from %s — returning empty reply.",
                    resp.status_code,
                    self.base_url,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return str(resp.json().get("response") or "")
        except Exception as exc:
            logger.warning(
                "OllamaProvider: async generate failed (%s) — returning empty reply.",
                exc,
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)


class OpenAIProvider:
    """Cloud-LLM provider backed by OpenAI's Chat Completions REST endpoint.

    Duck-typed identically to ``OllamaProvider`` — same ``generate_content`` /
    ``generate_content_async`` surface and the same "" error contract (returns
    "" on ANY failure, WARNING-logged — CODING_POLICY §5.6, never raises), so
    callers degrade the same way a failed Gemini/Ollama call does.

    Cloud-native (not a local runtime) → ALLOWED on Cloud Run: no
    ``_guard_cloud_ollama``. Decoding is DETERMINISTIC (temperature 0.0). Uses
    ``httpx`` REST directly — no ``openai`` SDK dependency is added.
    """

    _URL = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self, model: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        self.model = model or resolved_model_name(
            "openai"
        )  # #2962: one source of truth
        self.api_key = api_key or os.getenv("OPENAI_API_KEY") or ""

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_output_tokens,
            "temperature": 0.0,
        }

    @staticmethod
    def _extract(data: Dict[str, Any]) -> str:
        return str(data["choices"][0]["message"]["content"] or "")

    # #4939: Websuche nur ueber die Responses API (Chat Completions sucht nur mit eigenen
    # Suchmodellen). Ohne ``temperature``: Reasoning-Modelle lehnen sie dort ab.
    _SUCH_URL = "https://api.openai.com/v1/responses"

    def _such_payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model,
            "input": prompt,
            "tools": [{"type": "web_search"}],
            "max_output_tokens": max_output_tokens,
        }

    @staticmethod
    def _such_extract(data: Dict[str, Any]) -> Optional[SuchAntwort]:
        """Text aus den ``message``-Items, Quellen aus ``url_citation``-Annotationen."""
        texte: List[str] = []
        urls: List[Any] = []
        for item in data.get("output") or []:
            if item.get("type") != "message":
                continue
            for teil in item.get("content") or []:
                if teil.get("type") != "output_text":
                    continue
                texte.append(str(teil.get("text") or ""))
                urls.extend(
                    a.get("url")
                    for a in teil.get("annotations") or []
                    if a.get("type") == "url_citation"
                )
        return SuchAntwort("".join(texte), _eindeutig(urls))

    async def generate_with_search_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> SuchAntwort:
        """#4939: ein Aufruf MIT Websuche (opt-in, nur der Prompt-Lauf). Wirft nie."""
        if not self.api_key:
            logger.warning(
                "OpenAIProvider: OPENAI_API_KEY missing — web search skipped, empty reply."
            )
            record_call(time.perf_counter(), RuntimeError("missing_api_key"))
            return SuchAntwort()
        return await _suche_async(
            "OpenAIProvider",
            self._SUCH_URL,
            self._headers(),
            self._such_payload(prompt, max_output_tokens),
            self._such_extract,
        )

    def generate_content(self, prompt: str, max_output_tokens: int = 512) -> str:
        # ADR-OBS-01 / PR D: time the real generate call (PURE OBSERVATION) —
        # the record_call is fail-safe and can never alter this "" contract.
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            if not self.api_key:
                logger.warning(
                    "OpenAIProvider: OPENAI_API_KEY missing — returning empty reply."
                )
                _obs_exc = RuntimeError("missing_api_key")
                return ""
            import httpx

            resp = httpx.post(
                self._URL,
                headers=self._headers(),
                json=self._payload(prompt, max_output_tokens),
                timeout=_CLOUD_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.warning(
                    "OpenAIProvider: HTTP %s from OpenAI — returning empty reply.",
                    resp.status_code,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return self._extract(resp.json())
        except Exception as exc:
            logger.warning(
                "OpenAIProvider: generate failed (%s) — returning empty reply.", exc
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)

    async def generate_content_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> str:
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            if not self.api_key:
                logger.warning(
                    "OpenAIProvider: OPENAI_API_KEY missing — returning empty reply."
                )
                _obs_exc = RuntimeError("missing_api_key")
                return ""
            import httpx

            # Per-call client: a cached AsyncClient binds to the first event loop
            # it runs on (same bug class as the per-loop lock in
            # core/database/session.py). Connection setup is negligible here.
            async with httpx.AsyncClient(timeout=_CLOUD_TIMEOUT_S) as client:
                resp = await client.post(
                    self._URL,
                    headers=self._headers(),
                    json=self._payload(prompt, max_output_tokens),
                )
            if resp.status_code != 200:
                logger.warning(
                    "OpenAIProvider: HTTP %s from OpenAI — returning empty reply.",
                    resp.status_code,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return self._extract(resp.json())
        except Exception as exc:
            logger.warning(
                "OpenAIProvider: async generate failed (%s) — returning empty reply.",
                exc,
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)


class AnthropicProvider:
    """Cloud-LLM provider backed by Anthropic's Messages REST endpoint.

    Duck-typed identically to ``OllamaProvider`` — same surface and the same ""
    error contract (returns "" on ANY failure, WARNING-logged — CODING_POLICY
    §5.6, never raises). Cloud-native → ALLOWED on Cloud Run (no
    ``_guard_cloud_ollama``). Deterministic (temperature 0.0). Uses ``httpx``
    REST directly — no ``anthropic`` SDK dependency is added.
    """

    _URL = "https://api.anthropic.com/v1/messages"
    _API_VERSION = "2023-06-01"  # pinned Anthropic API version header

    def __init__(
        self, model: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        self.model = model or resolved_model_name(
            "anthropic"
        )  # #2962: one source of truth
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY") or ""

    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self._API_VERSION,
            "Content-Type": "application/json",
        }

    def _payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model,
            "max_tokens": max_output_tokens,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }

    @staticmethod
    def _extract(data: Dict[str, Any]) -> str:
        return str(data["content"][0]["text"] or "")

    def _such_payload(self, prompt: str, max_output_tokens: int) -> Dict[str, Any]:
        return {
            **self._payload(prompt, max_output_tokens),
            "tools": [
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": WEBSUCHE_MAX_USES,
                }
            ],
        }

    @staticmethod
    def _such_extract(data: Dict[str, Any]) -> Optional[SuchAntwort]:
        """Nur die ``text``-Bloecke NACH dem letzten ``web_search_tool_result`` — die
        Ankuendigung vor der Suche ("I'll search ...") ist keine Antwort. ``pause_turn``
        (Suche nicht abgeschlossen) wird nicht fortgesetzt: Fehlschlag mit WARNING."""
        if data.get("stop_reason") == "pause_turn":
            logger.warning(
                "AnthropicProvider: web search ended with stop_reason=pause_turn "
                "(not continued) — returning empty reply."
            )
            return None
        bloecke = list(data.get("content") or [])
        start = 0
        for i, block in enumerate(bloecke):
            if block.get("type") == "web_search_tool_result":
                start = i + 1
        texte: List[str] = []
        urls: List[Any] = []
        for block in bloecke[start:]:
            if block.get("type") != "text":
                continue
            texte.append(str(block.get("text") or ""))
            urls.extend(
                c.get("url")
                for c in block.get("citations") or []
                if c.get("type") == "web_search_result_location"
            )
        return SuchAntwort("".join(texte), _eindeutig(urls))

    async def generate_with_search_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> SuchAntwort:
        """#4939: ein Aufruf MIT Websuche (opt-in, nur der Prompt-Lauf). Wirft nie."""
        if not self.api_key:
            logger.warning(
                "AnthropicProvider: ANTHROPIC_API_KEY missing — web search skipped, "
                "empty reply."
            )
            record_call(time.perf_counter(), RuntimeError("missing_api_key"))
            return SuchAntwort()
        return await _suche_async(
            "AnthropicProvider",
            self._URL,
            self._headers(),
            self._such_payload(prompt, max_output_tokens),
            self._such_extract,
        )

    def generate_content(self, prompt: str, max_output_tokens: int = 512) -> str:
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            if not self.api_key:
                logger.warning(
                    "AnthropicProvider: ANTHROPIC_API_KEY missing — returning empty reply."
                )
                _obs_exc = RuntimeError("missing_api_key")
                return ""
            import httpx

            resp = httpx.post(
                self._URL,
                headers=self._headers(),
                json=self._payload(prompt, max_output_tokens),
                timeout=_CLOUD_TIMEOUT_S,
            )
            if resp.status_code != 200:
                logger.warning(
                    "AnthropicProvider: HTTP %s from Anthropic — returning empty reply.",
                    resp.status_code,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return self._extract(resp.json())
        except Exception as exc:
            logger.warning(
                "AnthropicProvider: generate failed (%s) — returning empty reply.", exc
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)

    async def generate_content_async(
        self, prompt: str, max_output_tokens: int = 512
    ) -> str:
        _t0 = time.perf_counter()
        _obs_exc: Optional[BaseException] = None
        try:
            if not self.api_key:
                logger.warning(
                    "AnthropicProvider: ANTHROPIC_API_KEY missing — returning empty reply."
                )
                _obs_exc = RuntimeError("missing_api_key")
                return ""
            import httpx

            async with httpx.AsyncClient(timeout=_CLOUD_TIMEOUT_S) as client:
                resp = await client.post(
                    self._URL,
                    headers=self._headers(),
                    json=self._payload(prompt, max_output_tokens),
                )
            if resp.status_code != 200:
                logger.warning(
                    "AnthropicProvider: HTTP %s from Anthropic — returning empty reply.",
                    resp.status_code,
                )
                _obs_exc = RuntimeError(f"http_{resp.status_code}")
                return ""
            return self._extract(resp.json())
        except Exception as exc:
            logger.warning(
                "AnthropicProvider: async generate failed (%s) — returning empty reply.",
                exc,
            )
            _obs_exc = exc
            return ""
        finally:
            record_call(_t0, _obs_exc)


# Only the (stateless, cheap) Ollama + cloud providers are memoized; the gemini
# path delegates uncached so a None (key missing / transient build failure) is
# retried by gemini_client itself on the next call — never frozen by the seam.
_ollama_singleton: Optional[OllamaProvider] = None
_ollama_lock = threading.Lock()
_openai_singleton: Optional[OpenAIProvider] = None
_openai_lock = threading.Lock()
_anthropic_singleton: Optional[AnthropicProvider] = None
_anthropic_lock = threading.Lock()


def _get_ollama_singleton() -> OllamaProvider:
    global _ollama_singleton
    if _ollama_singleton is None:
        with _ollama_lock:
            if _ollama_singleton is None:
                _ollama_singleton = OllamaProvider()
    return _ollama_singleton


def _get_openai_singleton() -> OpenAIProvider:
    global _openai_singleton
    if _openai_singleton is None:
        with _openai_lock:
            if _openai_singleton is None:
                _openai_singleton = OpenAIProvider()
    return _openai_singleton


def _get_anthropic_singleton() -> AnthropicProvider:
    global _anthropic_singleton
    if _anthropic_singleton is None:
        with _anthropic_lock:
            if _anthropic_singleton is None:
                _anthropic_singleton = AnthropicProvider()
    return _anthropic_singleton


def get_llm_provider() -> Optional[Any]:
    """Resolve the process-wide LLM provider from ``LLM_PROVIDER``.

    Returns None exactly when today's ``get_gemini_instance()`` returns None
    (no key / SDK unavailable) — callers already handle that.
    """
    # ADR-014: LLM_PROVIDER is an env boundary read in this seam (deliberately
    # NOT exposed via get_config() — test_oss_config_parity guards only the
    # get_config() surface, and centralising it would add config.oss.py drift).
    provider = resolved_provider_name()
    if provider == "ollama":
        _guard_cloud_ollama()
        return _get_ollama_singleton()
    # Cloud providers (P2, #1406): cloud-native, ALLOWED on Cloud Run — no
    # _guard_cloud_ollama. Memoized like Ollama (stateless + cheap to build).
    if provider == "openai":
        return _get_openai_singleton()
    if provider == "anthropic":
        return _get_anthropic_singleton()
    if provider not in ("", "gemini"):
        logger.warning(
            "Unknown LLM_PROVIDER=%r — falling back to the gemini path.",
            provider,
        )
    # Lazy import: G4a-2 routes gemini_client's chat helpers through this
    # factory — a top-level import here would create an import cycle.
    from core.gemini_client import get_gemini_instance

    return get_gemini_instance()


def get_llm_provider_fuer(anbieter: str) -> Optional[Any]:
    """#4939: der Provider eines GENANNTEN Anbieters, unabhaengig von ``LLM_PROVIDER``.

    Nur der LLM-Prompt-Lauf nutzt ihn (``LLM_RANKING_ANBIETER``); alle anderen Aufgaben
    bleiben bei ``get_llm_provider``. Fehlt der Schluessel des Anbieters, liefert sie None
    mit WARNING — nie einen anderen Anbieter (kein stiller Wechsel). ``ollama`` bleibt
    unter Cloud Run verweigert (``_guard_cloud_ollama``, BORA).

    Der Schluessel wird VOR dem Singleton geprueft (Review #4947, P2-03): ohne ihn wird
    keine Instanz mit leerem Schluessel gemerkt, ein spaeter gesetzter wirkt beim
    naechsten Aufruf.
    """
    if anbieter == "ollama":
        _guard_cloud_ollama()
        return _get_ollama_singleton()
    cloud: Dict[str, tuple] = {
        "openai": ("OPENAI_API_KEY", _get_openai_singleton),
        "anthropic": ("ANTHROPIC_API_KEY", _get_anthropic_singleton),
    }
    if anbieter in cloud:
        env, singleton = cloud[anbieter]
        if not os.getenv(env):
            logger.warning(
                "get_llm_provider_fuer: %s missing — no provider for %r.", env, anbieter
            )
            return None
        return singleton()
    if anbieter == "gemini":
        from core.gemini_client import get_gemini_instance

        prov = get_gemini_instance()
        if prov is None:
            logger.warning(
                "get_llm_provider_fuer: Gemini unavailable (GEMINI_API_KEY missing or "
                "SDK init failed) — no provider for 'gemini'."
            )
        return prov
    logger.warning(
        "get_llm_provider_fuer: unknown provider %r — no provider.", anbieter
    )
    return None


def reset_llm_provider() -> None:
    """Test hook — drop the memoized Ollama/cloud providers."""
    global _ollama_singleton, _openai_singleton, _anthropic_singleton
    with _ollama_lock:
        _ollama_singleton = None
    with _openai_lock:
        _openai_singleton = None
    with _anthropic_lock:
        _anthropic_singleton = None


__all__ = [
    "OllamaProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "SuchAntwort",
    "WEBSUCHE_MAX_USES",
    "get_llm_provider",
    "get_llm_provider_fuer",
    "reset_llm_provider",
]
