# tests/unit/test_report_narrator.py
# RPT-5 (#2004, Epic #1998) — the LLM narrator that fills the three prose slots
# (thesis / catalysts / risks) the deterministic renderer leaves as placeholders.
#
# The LLM is NON-DETERMINISTIC, so every test here injects a FAKE provider
# (a ``generate_content``-shaped object OR a bare callable) — no network, no
# clock, no randomness, no real model is ever called (the real Ollama run is
# proven in scratchpad/step0c_polished.py and referenced in the PR, NOT run in
# CI). Test surfaces:
#   1. narrate() fills all three slots into a report that audits clean.
#   2. the serialized FactSet is fed to the model as the ONLY allowed fact source.
#   3. a "Hier sind..." preamble is stripped from a slot.
#   4. a banned score-characterization word ("zuverlaessig") triggers one
#      nachbesserung; if it persists the slot abstains ("").
#   5. a fabricated number -> auditor STRIKE -> one re-narrate; if it persists
#      the offending slot abstains ("") — a deterministic abstain, never an
#      invented claim, and the composed report audits clean again.
#   6. determinism: a fixed provider yields an identical slot dict.
#   7. the real get_llm_provider() seam is used when llm=None (no hardcode).
#   8. a bare callable provider is accepted; no provider -> all slots abstain.
#   9. the auditor is injectable (the re-narrate loop uses it).
#  10. BORA: the module hardcodes no Ollama/Gemini and touches no
#      Redis/SQLite/DEPLOYMENT_MODE.

import json
import os
from datetime import date
from unittest.mock import patch

from core.report import narrate as narrate_from_pkg
from core.report.auditor import audit
from core.report.fact_set import InMemoryDataContext, NewsItem, build_fact_set
from core.report.narrator import narrate
from core.report.render import render

# ---------------------------------------------------------------------------
# Fixtures — the SAME committed real NVDA slices RPT-1/RPT-3 use.
# ---------------------------------------------------------------------------
_FX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "report")
_CARD = os.path.join(
    os.path.dirname(__file__), "..", "..", "core", "report", "model_card.json"
)
AS_OF = date(2026, 5, 1)


def _load_json(name):
    with open(os.path.join(_FX, name), encoding="utf-8") as f:
        return json.load(f)


def _load_card():
    with open(_CARD, encoding="utf-8") as f:
        return json.load(f)


def _ctx(**overrides):
    kw = {
        "bars": [
            (date.fromisoformat(d), float(c)) for d, c in _load_json("nvda_bars.json")
        ],
        "lstm_series": [
            (date.fromisoformat(d), float(s))
            for d, s in _load_json("nvda_lstm_series.json")
        ],
        "lstm_cross_section": {
            sym: float(sc) for sym, sc in _load_json("nvda_lstm_xsec.json").items()
        },
        "model_card": _load_card(),
        "fundamentals": None,
        "news": [
            NewsItem(
                title=n["title"],
                published_utc=n["published_utc"],
                publisher=n["publisher"],
                url=n["url"],
            )
            for n in _load_json("nvda_news.json")
        ],
        "alt_signals": {},
    }
    kw.update(overrides)
    return InMemoryDataContext(**kw)


def _fs(**overrides):
    return build_fact_set("NVDA", AS_OF, _ctx(**overrides))


# ---------------------------------------------------------------------------
# Fake providers (a real generate_content surface + a bare callable).
# ---------------------------------------------------------------------------
# Clean prose is deliberately number-free and entity-free so the REAL auditor
# sees only the deterministic sections' facts -> a clean, sourced report.
_CLEAN = {
    "thesis": (
        "Das Modell ordnet den Titel relativ zum Universum weit oben ein, "
        "waehrend der Kurs zuletzt vom Hoch zurueckkam. Der Trend bleibt oberhalb "
        "der gleitenden Mittel intakt."
    ),
    "catalysts": (
        "Der anstehende Ergebnistermin liegt kurz nach dem Stichtag und bringt "
        "Ereignis-Unsicherheit. Die uebrigen Meldungen zeichnen ein gemischtes "
        "Umfeld ohne eindeutige Richtung."
    ),
    "risks": (
        "Das binaere Ergebnis-Ereignis nach dem Stichtag ist das Hauptrisiko, "
        "flankiert vom Wettbewerb. Hinzu kommt das Ausfuehrungsrisiko der "
        "Strategie nach Kosten."
    ),
}


def _slot_of(prompt):
    for name in ("thesis", "catalysts", "risks"):
        if f"SLOT: {name}" in prompt:
            return name
    return "thesis"


class _RecordingLLM:
    """Provider-shaped fake: generate_content(prompt, max_output_tokens) -> str."""

    def __init__(self, responder):
        self._responder = responder
        self.prompts = []

    def generate_content(self, prompt, max_output_tokens=512):
        n = len(self.prompts)
        self.prompts.append(prompt)
        return self._responder(prompt, n)


def _clean_responder(prompt, _n):
    return _CLEAN[_slot_of(prompt)]


def _preamble_responder(prompt, _n):
    body = _CLEAN[_slot_of(prompt)]
    return "Hier sind die zwei Saetze:\n\n" + body


def _banned_always_responder(prompt, _n):
    if _slot_of(prompt) == "thesis":
        return "Der Score ist ausgesprochen zuverlaessig und ehrlich."
    return _CLEAN[_slot_of(prompt)]


def _banned_then_clean_responder(prompt, _n):
    if _slot_of(prompt) == "thesis" and "KORREKTUR" not in prompt:
        return "Der Score ist ausgesprochen zuverlaessig und ehrlich."
    return _CLEAN[_slot_of(prompt)]


def _fabricate_always_responder(prompt, _n):
    if _slot_of(prompt) == "thesis":
        return "Das faire Kursziel liegt bei 999.99 USD und steigt weiter."
    return _CLEAN[_slot_of(prompt)]


def _fabricate_then_clean_responder(prompt, _n):
    if _slot_of(prompt) == "thesis" and "KORREKTUR" not in prompt:
        return "Das faire Kursziel liegt bei 999.99 USD und steigt weiter."
    return _CLEAN[_slot_of(prompt)]


# ---------------------------------------------------------------------------
# 1. narrate() fills all three slots into a report that audits clean.
# ---------------------------------------------------------------------------
def test_fills_all_three_slots_into_a_clean_report():
    fs = _fs()
    slots = narrate(fs, llm=_RecordingLLM(_clean_responder))
    assert set(slots) == {"thesis", "catalysts", "risks"}
    assert all(slots[k].strip() for k in slots)
    # the composed report is fully sourced (the auditor finds zero strikes)
    result = audit(render(fs, narration=slots), fs)
    assert result.integrity_ok, result.summary()
    # the prose actually landed in the rendered report
    assert slots["thesis"] in render(fs, narration=slots)


def test_narrate_is_exported_from_the_package():
    assert narrate_from_pkg is narrate


# ---------------------------------------------------------------------------
# 2. The serialized FactSet is the ONLY allowed fact source (fed to the model).
# ---------------------------------------------------------------------------
def test_prompt_carries_the_factset_as_the_only_source():
    fs = _fs()
    llm = _RecordingLLM(_clean_responder)
    narrate(fs, llm=llm)
    joined = "\n".join(llm.prompts)
    # identity + the load-bearing numbers the deterministic sections rely on
    assert "NVDA" in joined
    assert "2026-05-01" in joined
    assert "198.45" in joined
    assert "4.10" in joined
    assert "95.7" in joined
    assert "486" in joined
    assert "1.55 -> 2.52 -> 3.29 -> 3.58 -> 4.10" in joined
    # the honest net<EW reliability facts
    assert "0.758" in joined and "0.944" in joined
    # a real news title (the only allowed catalyst source)
    assert "NVIDIA Sets Conference Call for First-Quarter Financial Results" in joined
    # the deterministic-first instruction is present
    assert "FACT SET" in joined
    assert "EINZIGE" in joined or "einzige" in joined.lower()


# ---------------------------------------------------------------------------
# 3. A "Hier sind..." preamble is stripped.
# ---------------------------------------------------------------------------
def test_preamble_is_stripped_from_the_slot():
    fs = _fs()
    slots = narrate(fs, llm=_RecordingLLM(_preamble_responder))
    for k in slots:
        assert "Hier sind" not in slots[k]
        assert not slots[k].lower().startswith("hier ")
    # the real content survived
    assert "Das Modell ordnet den Titel" in slots["thesis"]


# ---------------------------------------------------------------------------
# 4. Banned score-characterization word -> nachbesserung, else abstain.
# ---------------------------------------------------------------------------
def _has_banned(text):
    low = text.lower()
    return any(b in low for b in ("zuverlaess", "zuverläss", "ehrlich"))


def test_banned_word_triggers_renarrate_then_clean():
    fs = _fs()
    llm = _RecordingLLM(_banned_then_clean_responder)
    slots = narrate(fs, llm=llm)
    assert not _has_banned(slots["thesis"])
    assert slots["thesis"].strip()  # nachbesserung succeeded, not abstained
    # a KORREKTUR re-prompt was actually issued for the thesis slot
    assert any("KORREKTUR" in p and "SLOT: thesis" in p for p in llm.prompts)


def test_banned_word_persists_then_slot_abstains():
    fs = _fs()
    slots = narrate(fs, llm=_RecordingLLM(_banned_always_responder))
    assert slots["thesis"] == ""  # deterministic abstain, no banned prose
    # the other slots are unaffected
    assert slots["catalysts"].strip() and slots["risks"].strip()
    # abstain renders as the honest placeholder, report still audits clean
    result = audit(render(fs, narration=slots), fs)
    assert result.integrity_ok, result.summary()


# ---------------------------------------------------------------------------
# 5. Fabricated number -> auditor STRIKE -> re-narrate; persist -> abstain.
# ---------------------------------------------------------------------------
def test_fabricated_number_strikes_then_renarrate_fixes():
    fs = _fs()
    llm = _RecordingLLM(_fabricate_then_clean_responder)
    slots = narrate(fs, llm=llm)
    assert "999.99" not in slots["thesis"]
    assert slots["thesis"].strip()  # re-narrate produced clean, sourced prose
    result = audit(render(fs, narration=slots), fs)
    assert result.integrity_ok, result.summary()
    assert any("KORREKTUR" in p and "SLOT: thesis" in p for p in llm.prompts)


def test_fabricated_number_persists_then_abstains_and_report_is_clean():
    fs = _fs()
    slots = narrate(fs, llm=_RecordingLLM(_fabricate_always_responder))
    # the offending slot abstains rather than emit an unsourced number
    assert slots["thesis"] == ""
    assert "999.99" not in "".join(slots.values())
    # with the abstained slot rendered as a placeholder, the report is clean
    result = audit(render(fs, narration=slots), fs)
    assert result.integrity_ok, result.summary()


# ---------------------------------------------------------------------------
# 6. Determinism — a fixed provider yields an identical slot dict.
# ---------------------------------------------------------------------------
def test_deterministic_with_a_fixed_provider():
    fs = _fs()
    a = narrate(fs, llm=_RecordingLLM(_clean_responder))
    b = narrate(fs, llm=_RecordingLLM(_clean_responder))
    assert a == b


# ---------------------------------------------------------------------------
# 7. The real get_llm_provider() seam is used when llm=None (no hardcode).
# ---------------------------------------------------------------------------
def test_uses_get_llm_provider_seam_when_llm_is_none():
    fs = _fs()
    fake = _RecordingLLM(_clean_responder)
    with patch("core.llm.provider.get_llm_provider", return_value=fake) as seam:
        slots = narrate(fs)  # llm defaults to the real seam
    seam.assert_called_once()
    assert fake.prompts, "the seam-resolved provider was never called"
    assert all(slots[k].strip() for k in slots)


def test_no_provider_from_seam_abstains_all_slots():
    fs = _fs()
    with patch("core.llm.provider.get_llm_provider", return_value=None):
        slots = narrate(fs)
    assert slots == {"thesis": "", "catalysts": "", "risks": ""}
    # placeholders remain, so the report is still deterministic + clean
    assert audit(render(fs, narration=slots), fs).integrity_ok


# ---------------------------------------------------------------------------
# 8. A bare callable provider is accepted.
# ---------------------------------------------------------------------------
def test_bare_callable_provider_is_accepted():
    fs = _fs()

    def fn(prompt):
        return _CLEAN[_slot_of(prompt)]

    slots = narrate(fs, llm=fn)
    assert all(slots[k].strip() for k in slots)
    assert audit(render(fs, narration=slots), fs).integrity_ok


# ---------------------------------------------------------------------------
# 9. The auditor is injectable — the re-narrate loop uses the injected gate.
# ---------------------------------------------------------------------------
def test_injected_auditor_is_used_by_the_renarrate_loop():
    fs = _fs()
    calls = {"n": 0}

    def spy_auditor(report_md, fact_set):
        calls["n"] += 1
        return audit(report_md, fact_set)

    slots = narrate(fs, llm=_RecordingLLM(_clean_responder), auditor=spy_auditor)
    assert calls["n"] >= 1  # the injected auditor gated the composed report
    assert all(slots[k].strip() for k in slots)


# ---------------------------------------------------------------------------
# 10. BORA — no hardcoded Ollama/Gemini, no Redis/SQLite/DEPLOYMENT_MODE.
# ---------------------------------------------------------------------------
def _executable_code_tokens(path):
    """The module's real code identifiers, with comments + string literals removed.

    Naming a provider or ``Redis`` in a docstring that DECLARES the BORA contract
    (house style, cf. auditor.py/render.py) is fine and desirable; a concrete
    endpoint, SDK import, or infra call in *executable code* is the real
    violation. Stripping COMMENT / STRING / FSTRING tokens targets exactly that.
    """
    import io
    import tokenize

    names = []
    with open(path, encoding="utf-8") as f:
        for tok in tokenize.generate_tokens(f.readline):
            kind = tokenize.tok_name[tok.type]
            if kind in ("COMMENT", "STRING") or kind.startswith("FSTRING"):
                continue
            names.append(tok.string)
    return " ".join(names).lower()


def test_module_is_bora_clean_and_uses_the_seam():
    import core.report.narrator as mod

    code = _executable_code_tokens(mod.__file__)
    # No hardcoded provider endpoint / SDK / concrete client in executable code.
    for forbidden in (
        "ollama",
        "gemini",
        "openai",
        "anthropic",
        "11434",
        "generativelanguage",
        "genai",
        "httpx",
        "requests",
        "urllib",
    ):
        assert forbidden not in code, f"hardcoded provider usage: {forbidden!r}"
    # No edition / infra coupling in executable code.
    for forbidden in ("redis", "sqlite", "deployment_mode", "k_service"):
        assert forbidden not in code, f"edition/infra leak: {forbidden!r}"
    # The sanctioned seam IS referenced in code (the LLM is reached only via it).
    assert "get_llm_provider" in code
