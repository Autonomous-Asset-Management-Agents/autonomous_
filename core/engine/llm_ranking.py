"""LLM-Ranking-Quelle — der gespeicherte Prompt des Nutzers als Vorauswahl (#4648, Epic #4638).

Zweite Ranking-Quelle neben dem LSTM-Panel (`core/report/lstm_panel_store`): Der Prompt des
Nutzers wird ueber die einzige LLM-Seam (`core.llm.provider.get_llm_provider`) abgefragt, jeder
gelieferte Ticker wird geprueft, und die Rangliste wird auf Top-K gekappt. `als_cross_section`
bildet sie auf das `xsec`-Format ab, das `rank_funnel.apply_rank_cache_funnel` unveraendert
annimmt — die Held-Invariante (Exits verhungern nie) bleibt damit an genau einer Stelle.

Harte NFR: kein Draht vom LLM zum Broker. Dieses Modul liefert nur eine Rangliste; es
importiert weder den Order-Executor noch den SignalCandidate-Vertrag
(`tests/unit/test_llm_ranking_kein_orderpfad.py`).

#4960: optional mit der Websuche des Anbieters (``websuche=True`` und ein Provider mit
``generate_with_search_async``) und mit einem eigenen Anbieter (``anbieter``, aufgeloest ueber
``get_llm_provider_fuer``). Die Ticker kommen weiter allein aus dem Antworttext; die Quellen
landen nur im Protokoll.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from core.llm.health import resolved_provider_name
from core.llm.provider import get_llm_provider, get_llm_provider_fuer

logger = logging.getLogger(__name__)

# Feste, inhaltsleere Formatklausel: legt nur das Ausgabeformat fest, keine Anlageidee
# (ADR-027 Regel 1 — keine vom Anbieter vorgefertigten Prompts).
FORMATKLAUSEL = (
    "\n\nAntwortformat: Gib ausschliesslich Aktien-Ticker zurueck, kommagetrennt, "
    "in absteigender Rangfolge, ohne weiteren Text."
)
TICKER_MUSTER = re.compile(r"^[A-Z][A-Z.\-]{0,9}$")
# Ein Ticker kostet wenige Tokens; 512 reicht fuer die Obergrenze ROUND_TABLE_TOP_K_EVAL=60.
MAX_OUTPUT_TOKENS = 512
TIMEOUT_S = 120.0
# #4960: hoechstens so viele Quell-URLs je Lauf ins Protokoll (dedupliziert, Antwortreihenfolge).
MAX_QUELLEN = 20
# #4960: Wert von ``LLM_RANKING_ANBIETER``, der dem Systemanbieter (``LLM_PROVIDER``) folgt.
ANBIETER_SYSTEM = "system"
_TRENNER = re.compile(r"[,;\n]")


@dataclass
class LlmRankingErgebnis:
    ok: bool
    rangliste: List[str] = field(default_factory=list)
    verworfen: Dict[str, str] = field(default_factory=dict)
    modell: Optional[str] = None
    anbieter: Optional[str] = None
    prompt_hash: str = ""
    fehler: Optional[str] = None
    # #4960: ``anbieter`` bleibt der Klassenname des Providers; ``kanonischer_anbieter`` ist der
    # effektive Anbieter ("gemini", "ollama", ...; None ohne Angabe an ``frage_llm_ranking``).
    kanonischer_anbieter: Optional[str] = None
    websuche: bool = False
    quellen: List[str] = field(default_factory=list)


def _parse(antwort: str) -> List[str]:
    """Tokens in Antwortreihenfolge, gross geschrieben, ohne Duplikate."""
    gesehen: Dict[str, None] = {}
    for roh in _TRENNER.split(antwort):
        token = roh.strip().upper()
        if token:
            gesehen.setdefault(token, None)
    return list(gesehen)


def _pruefe(
    tokens: List[str],
    universum: Set[str],
    top_k: int,
    bekannte_symbole: Optional[Set[str]],
) -> tuple[List[str], Dict[str, str]]:
    rangliste: List[str] = []
    verworfen: Dict[str, str] = {}
    for token in tokens:
        if not TICKER_MUSTER.fullmatch(token):
            verworfen[token] = "formfehler"
        elif token in universum:
            if len(rangliste) < top_k:
                rangliste.append(token)
            else:
                verworfen[token] = "ueber_top_k"
        elif bekannte_symbole is not None and token not in bekannte_symbole:
            verworfen[token] = "unbekannt"
        else:
            verworfen[token] = "nicht_im_universum"
    return rangliste, verworfen


def pruefe_symbolliste(
    text: str, universum: Set[str], top_k: int
) -> tuple[List[str], Dict[str, str]]:
    """#4861: die eigene Liste des Nutzers — dieselbe Zerlegung und Pruefung wie eine
    LLM-Antwort, ohne Symbolverzeichnis: ein wohlgeformter Ticker ausserhalb des Universums
    ist ``nicht_im_universum``. Keine zweite Logik."""
    return _pruefe(_parse(text or ""), universum, top_k, None)


def _quellen(urls: Any) -> List[str]:
    """Quell-URLs dedupliziert in Antwortreihenfolge, ohne Leere, auf MAX_QUELLEN gekappt."""
    gesehen: Dict[str, None] = {}
    for url in urls or []:
        if isinstance(url, str) and url.strip():
            gesehen.setdefault(url.strip(), None)
    return list(gesehen)[:MAX_QUELLEN]


async def frage_llm_ranking(
    prompt: str,
    universum: Set[str],
    top_k: int,
    provider: Any = None,
    bekannte_symbole: Optional[Set[str]] = None,
    timeout_s: float = TIMEOUT_S,
    anbieter: Optional[str] = None,
    websuche: bool = False,
) -> LlmRankingErgebnis:
    """Fragt den gespeicherten Prompt ab und liefert die gepruefte Rangliste. Wirft nie.

    Args:
        prompt:           der gespeicherte Prompt des Nutzers (unveraendert gehasht).
        universum:        die handelbaren Symbole dieses Zyklus (`z.symbols_to_process`).
        top_k:            Laenge der Rangliste (`ROUND_TABLE_TOP_K_EVAL` aus der Konfiguration).
        provider:         LLM-Provider; None → `get_llm_provider()`.
        bekannte_symbole: optionale Menge real existierender Ticker. Ist sie gesetzt, wird ein
                          wohlgeformter Ticker ausserhalb davon als `unbekannt` verworfen
                          (Halluzination), sonst als `nicht_im_universum`.
        anbieter:         #4960 ``LLM_RANKING_ANBIETER``. None/``system`` → Systemanbieter wie
                          bisher; sonst `get_llm_provider_fuer(anbieter)`. Fehlt dessen
                          Schluessel: Fehlschlag `kein_schluessel`, KEIN Rueckfall auf einen
                          anderen Anbieter.
        websuche:         #4960 Suchpfad, wenn der Provider `generate_with_search_async` hat;
                          sonst der alte Aufruf (und `websuche=False` im Ergebnis).

    Fehlschlag (`ok=False`, WARNING): kein Provider, Timeout, Exception, leere Antwort oder
    keine einzige gueltige Antwort — nie ein stilles leeres Ranking.
    """
    prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    # #4960: auf ALLEN Pfaden gesetzt, auch bei einem Fehlschlag (Review #4947, P2-02).
    kanonisch = resolved_provider_name() if anbieter == ANBIETER_SYSTEM else anbieter
    if top_k <= 0:
        logger.warning(
            "LlmRanking: ungueltiges top_k (%d <= 0) → Fehlschlag (Rueckfall beim Aufrufer)",
            top_k,
        )
        return LlmRankingErgebnis(
            ok=False,
            prompt_hash=prompt_hash,
            fehler="ungueltiges_top_k",
            kanonischer_anbieter=kanonisch,
        )
    if provider is None and anbieter not in (None, ANBIETER_SYSTEM):
        provider = get_llm_provider_fuer(anbieter)
        if provider is None:
            logger.warning(
                "LlmRanking: kein Schluessel fuer den gewaehlten Anbieter %r → Fehlschlag, "
                "kein Wechsel auf einen anderen Anbieter (Rueckfall beim Aufrufer)",
                anbieter,
            )
            return LlmRankingErgebnis(
                ok=False,
                anbieter=anbieter,
                prompt_hash=prompt_hash,
                fehler="kein_schluessel",
                kanonischer_anbieter=kanonisch,
            )
    if provider is None:
        provider = get_llm_provider()
    if provider is None:
        logger.warning(
            "LlmRanking: kein LLM-Provider verfuegbar → Fehlschlag (Rueckfall beim Aufrufer)"
        )
        return LlmRankingErgebnis(
            ok=False,
            prompt_hash=prompt_hash,
            fehler="kein_provider",
            kanonischer_anbieter=kanonisch,
        )

    modell = getattr(provider, "model", None)
    klasse = type(provider).__name__
    mit_suche = websuche and hasattr(provider, "generate_with_search_async")
    quellen: List[str] = []

    def _fehlschlag(
        grund: str, verworfen: Optional[Dict[str, str]] = None
    ) -> LlmRankingErgebnis:
        return LlmRankingErgebnis(
            ok=False,
            verworfen=verworfen or {},
            modell=modell,
            anbieter=klasse,
            prompt_hash=prompt_hash,
            fehler=grund,
            kanonischer_anbieter=kanonisch,
            websuche=mit_suche,
            quellen=list(quellen),
        )

    try:
        if mit_suche:
            such = await asyncio.wait_for(
                provider.generate_with_search_async(
                    prompt + FORMATKLAUSEL, max_output_tokens=MAX_OUTPUT_TOKENS
                ),
                timeout=timeout_s,
            )
            antwort, quellen = such.text, _quellen(such.quellen)
        else:
            antwort = await asyncio.wait_for(
                provider.generate_content_async(
                    prompt + FORMATKLAUSEL, max_output_tokens=MAX_OUTPUT_TOKENS
                ),
                timeout=timeout_s,
            )
    except asyncio.TimeoutError:
        logger.warning(
            "LlmRanking: Timeout nach %.0fs (%s) → Fehlschlag (Rueckfall beim Aufrufer)",
            timeout_s,
            klasse,
        )
        return _fehlschlag("timeout")
    except Exception as exc:  # noqa: BLE001 — die Quelle wirft nie
        logger.warning(
            "LlmRanking: Abfrage fehlgeschlagen (%s: %s) → Fehlschlag (Rueckfall beim Aufrufer)",
            type(exc).__name__,
            exc,
        )
        return _fehlschlag(type(exc).__name__)

    tokens = _parse(antwort or "")
    if not tokens:
        logger.warning(
            "LlmRanking: leere Antwort (%s) → Fehlschlag (Rueckfall beim Aufrufer)",
            klasse,
        )
        return _fehlschlag("leere_antwort")

    rangliste, verworfen = _pruefe(tokens, universum, top_k, bekannte_symbole)
    if not rangliste:
        logger.warning(
            "LlmRanking: keine gueltigen Ticker (%d verworfen) → Fehlschlag (Rueckfall beim Aufrufer)",
            len(verworfen),
        )
        return _fehlschlag("keine_gueltigen_ticker", verworfen)

    return LlmRankingErgebnis(
        ok=True,
        rangliste=rangliste,
        verworfen=verworfen,
        modell=modell,
        anbieter=klasse,
        prompt_hash=prompt_hash,
        kanonischer_anbieter=kanonisch,
        websuche=mit_suche,
        quellen=quellen,
    )


def als_cross_section(rangliste: List[str]) -> Dict[str, float]:
    """Rangposition → absteigender Score (n, n-1, …, 1) im `xsec`-Format des Funnels."""
    n = len(rangliste)
    return {symbol: float(n - i) for i, symbol in enumerate(rangliste)}
