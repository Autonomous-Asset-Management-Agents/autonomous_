# tests/unit/test_report_band_claims.py
# ADR-AUDIT-BAND-01 — the auditor must catch a QUALITATIVE rank-band claim that
# contradicts the cross-sectional percentile.
#
# The defect this pins (measured on a real LYB note, 2026-07-17): the narrator
# wrote into the thesis slot
#
#     "LYB liegt im Top-Quintil des Modells, was darauf hindeutet, dass das
#      Unternehmen eine starke Position innerhalb seines Universums einnimmt."
#
# LYB was rank 372 of 499 -> the 25.5th percentile. Bottom quartile. The prose
# asserts the exact OPPOSITE of the data, and the auditor passed it with ZERO
# flags, because every rule it had extracts a NUMBER/date/entity/url/source and
# that sentence contains no number. The claim reached the customer unmediated:
# the round table votes on data, not on report prose.
#
# Test surfaces (TDD, red first):
#   1. REGRESSION: the real LYB sentence at pct 25.5 -> FLAGGED.
#   2. The same sentence at pct 92 -> NOT flagged (it would be true).
#   3. PRECISION: the renderer's OWN honest band prose never flags, at every
#      band and at every threshold boundary. This is the test that matters most
#      -- a rule that flags the renderer makes every report red.
#   4. Every band at its boundary (79.9 vs 80.0, ...).
#   5. No percentile -> a band claim is FLAGGED (argued in auditor.py).
#   6. A report with no band words -> verdict unchanged.
#   7. WIRING: the flag reaches narrator._slots_with_flags so the retry fires.
#   8. DRIFT: the auditor's thresholds mirror render's quintile constants.
#
# Fully deterministic: no network / LLM / GPU / pandas / redis.

import importlib

import pytest
from test_report_auditor import _fs  # shared committed real NVDA slices

from core.report.auditor import CONTRADICTED, audit
from core.report.narrator import _critique_for, _slots_with_flags
from core.report.render import render

# ``core/report/__init__.py`` re-exports the render FUNCTION under the name
# ``core.report.render``, which shadows the submodule -- ``import
# core.report.render as m`` hands back the function, not the module. importlib
# resolves the real module object.
render_mod = importlib.import_module("core.report.render")

# The verbatim sentence from the real 2026-07-17 LYB note.
LYB_SENTENCE = (
    "LYB liegt im Top-Quintil des Modells, was darauf hindeutet, dass das "
    "Unternehmen eine starke Position innerhalb seines Universums einnimmt."
)


def _xsec(target_pct: float, symbol: str = "NVDA") -> dict:
    """A synthetic 1000-name panel placing ``symbol`` at EXACTLY ``target_pct``.

    ``cross_section_standing`` (lstm_panel_store.py:196) defines percentile as
    ``100 * (# scoring strictly BELOW) / n``. With n = 1000 the percentile lands
    on an exact 0.1 grid, which is what the boundary tests need: 79.9 and 80.0
    must be *representable*, or the boundary is not actually pinned. 999 peers
    hold the scores 0..998; the symbol slots in just under ``below``.
    """
    panel = {f"P{i}": float(i) for i in range(999)}
    below = round(target_pct * 10)  # n = 1000 -> 0.1 percentile granularity
    panel[symbol] = float(below) - 0.5
    return panel


def _fs_at(pct: float):
    return _fs(lstm_cross_section=_xsec(pct))


def _band_flags(report_md: str, fs):
    return [f for f in audit(report_md, fs).flags if f.kind == "band"]


# ---------------------------------------------------------------------------
# 1. THE REGRESSION — the real LYB sentence against the real percentile
# ---------------------------------------------------------------------------
def test_lyb_top_quintil_claim_at_bottom_quartile_is_flagged():
    """The exact defect: 'Top-Quintil' + 'starke Position' at the 25.5th pct."""
    fs = _fs_at(25.5)
    assert round(fs.lstm_xsec_percentile.value, 1) == 25.5
    flags = _band_flags(render(fs, narration={"thesis": LYB_SENTENCE}), fs)
    tokens = {f.token for f in flags}
    assert any("Quintil" in t for t in tokens), f"Top-Quintil not caught: {tokens}"
    assert any("starke Position" in t for t in tokens), f"not caught: {tokens}"
    assert all(f.verdict == CONTRADICTED for f in flags)
    assert not audit(render(fs, narration={"thesis": LYB_SENTENCE}), fs).integrity_ok


def test_lyb_sentence_is_not_flagged_when_it_would_be_true():
    """Same sentence, pct 92 -> a true claim must NOT flag (precision)."""
    fs = _fs_at(92)
    assert _band_flags(render(fs, narration={"thesis": LYB_SENTENCE}), fs) == []


# ---------------------------------------------------------------------------
# 2. PRECISION — the renderer's own honest output must never flag
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "pct",
    [0, 5, 10, 19, 20, 21, 25, 36, 50, 60, 75, 79, 80, 81, 90, 95, 99],
)
def test_renderer_own_prose_never_band_flags(pct):
    """The renderer derives its bands from the same percentile -- it cannot
    contradict itself. Includes 36 (the 'im **Mittelfeld**' case) and both
    quintile boundaries. A rule that trips here would redden every report."""
    fs = _fs_at(pct)
    assert _band_flags(render(fs), fs) == [], f"renderer flagged at pct={pct}"


def test_renderer_scenarios_counterfactuals_are_not_flagged():
    """Section 7 states DIRECTIONAL hypotheticals ('Aufstieg ins obere Fuenftel
    aus dem Mittelfeld' at mid-rank, 'Richtung Mittelfeld' at bottom). They are
    deterministic renderer output, contain no narration, and are not present-
    tense assertions -- they must never flag."""
    for pct in (10, 36):
        fs = _fs_at(pct)
        md = render(fs)
        assert "Szenarien" in md
        assert _band_flags(md, fs) == [], f"scenario table flagged at pct={pct}"


def test_renderer_has_no_band_words_when_there_is_no_rank():
    """The no-rank renderer abstains entirely, so rule 5 cannot hit it."""
    fs = _fs(lstm_cross_section={})
    assert fs.lstm_xsec_percentile.value is None
    assert _band_flags(render(fs), fs) == []


# ---------------------------------------------------------------------------
# 3. BOUNDARIES — every band at its threshold
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "phrase,ok_pct,bad_pct",
    [
        ("Top-Quintil", 80.0, 79.9),
        ("im oberen Fuenftel", 80.0, 79.9),
        ("in der Spitzengruppe", 80.0, 79.9),
        ("Spitzenreiter", 80.0, 79.9),
        ("im oberen Viertel", 75.0, 74.9),
        ("Top-Quartil", 75.0, 74.9),
        ("in der oberen Haelfte", 50.0, 49.9),
        ("im Mittelfeld", 50.0, 90.0),
        ("im Mittelfeld", 20.0, 10.0),
        ("in der unteren Haelfte", 50.0, 50.1),
        ("im unteren Viertel", 25.0, 25.1),
        ("im unteren Fuenftel", 20.0, 20.1),
        ("Schlusslicht", 20.0, 20.1),
        ("stark positioniert", 50.0, 49.9),
        ("schwach positioniert", 50.0, 50.1),
    ],
)
def test_band_boundaries(phrase, ok_pct, bad_pct):
    ok_fs = _fs_at(ok_pct)
    bad_fs = _fs_at(bad_pct)
    sentence = f"Der Titel steht {phrase} des Universums."
    assert (
        _band_flags(render(ok_fs, narration={"thesis": sentence}), ok_fs) == []
    ), f"{phrase!r} false-flagged at its OK boundary {ok_pct}"
    bad = _band_flags(render(bad_fs, narration={"thesis": sentence}), bad_fs)
    assert bad, f"{phrase!r} not caught at contradicting pct {bad_pct}"


def test_umlaut_and_transliterated_spellings_both_caught():
    """The narrator prompt uses ASCII transliterations ('zuverlaessig'), so the
    model emits both 'Fuenftel' and 'Fünftel'. Catch each."""
    fs = _fs_at(30)
    for phrase in ("im oberen Fünftel", "im oberen Fuenftel"):
        sentence = f"Der Titel liegt {phrase}."
        assert _band_flags(
            render(fs, narration={"thesis": sentence}), fs
        ), f"missed spelling {phrase!r}"


# ---------------------------------------------------------------------------
# 4. NO RANK — a band claim asserts a ranking that does not exist
# ---------------------------------------------------------------------------
def test_band_claim_without_any_rank_is_flagged():
    """Argued in auditor.py: percentile None means there IS no ranking (
    cross_section_standing returns (None, None, None) jointly), so a band claim
    is fabricated, not merely uncheckable. Mirrors ADR-RPT4-08."""
    fs = _fs(lstm_cross_section={})
    assert fs.lstm_xsec_percentile.value is None
    flags = _band_flags(render(fs, narration={"thesis": LYB_SENTENCE}), fs)
    assert flags, "a band claim with no panel at all must not pass"
    assert all("kein Rang" in f.note or "no rank" in f.note.lower() for f in flags)


# ---------------------------------------------------------------------------
# 5. NEUTRALITY — no band words changes nothing
# ---------------------------------------------------------------------------
def test_report_with_no_band_words_at_all_records_no_band_claim():
    """Text carrying no band phrase must not manufacture a band claim."""
    fs = _fs_at(25.5)
    md = "# NVDA\n\nDer Kurs liegt ueber dem kurzfristigen Schnitt.\n"
    res = audit(md, fs)
    assert not any(c.kind == "band" for c in res.claims)
    assert res.integrity_ok is True


def test_prose_without_band_words_leaves_the_verdict_unchanged():
    """A clean slot adds no band flag. NOTE: the rendered report still carries the
    RENDERER's own honest 'im **Mittelfeld**' at pct 25.5 — that is a band claim
    and it must be recorded as VALID, not suppressed. The audit trail should show
    the band was checked and held."""
    fs = _fs_at(25.5)
    plain = "Der Kurs liegt ueber dem kurzfristigen Schnitt."
    before = audit(render(fs), fs)
    md = render(fs, narration={"thesis": plain})
    after = audit(md, fs)
    assert _band_flags(md, fs) == []
    assert after.integrity_ok == before.integrity_ok is True
    valid_bands = [c for c in after.claims if c.kind == "band"]
    assert valid_bands and all(c.matched for c in valid_bands)


def test_schlusskurse_is_not_mistaken_for_schlussgruppe():
    """insight_patterns.py:207 emits 'Schlusskurse'. A bare 'Schluss' match would
    flag it as a bottom-quintile claim on every report that shows a pullback."""
    fs = _fs_at(95)
    md = "Der Rueckgang betraegt 3.0% ueber die letzten Schlusskurse."
    assert not any(c.kind == "band" for c in audit(md, fs).claims)


# ---------------------------------------------------------------------------
# 6. WIRING — the flag must actually reach the narrator's retry loop
# ---------------------------------------------------------------------------
def test_band_flag_reaches_narrator_retry_loop():
    """A rule that flags but never triggers a re-ask is a dead feature.

    ``_slots_with_flags`` attributes a flag to a slot by testing whether
    ``flag.token`` is a SUBSTRING of the slot text (narrator.py:357), so the
    band token must be the verbatim prose phrase."""
    fs = _fs_at(25.5)
    slots = {"thesis": LYB_SENTENCE, "catalysts": "", "risks": ""}
    result = audit(render(fs, narration=slots), fs)
    assert not result.integrity_ok

    culprits = _slots_with_flags(slots, result.flags)
    assert "thesis" in culprits, "the band flag never reached the retry loop"

    # The flag is attributed to the thesis by its OWN token, not by the
    # defensive catch-all that implicates every slot when nothing matches.
    band = [f for f in result.flags if f.kind == "band"]
    assert band
    for f in band:
        assert f.token in LYB_SENTENCE, f"token {f.token!r} is not verbatim prose"

    critique = _critique_for(slots, "thesis", result.flags)
    assert "Top-Quintil" in critique
    # The critique must state the TRUE standing, not the number-oriented
    # "not in the FACT SET" reason -- the percentile IS in the fact set.
    assert "25.5" in critique or "25,5" in critique


def test_clean_slot_is_not_implicated_by_another_slots_band_flag():
    fs = _fs_at(25.5)
    slots = {
        "thesis": LYB_SENTENCE,
        "catalysts": "Die Quartalszahlen stehen an.",
        "risks": "",
    }
    result = audit(render(fs, narration=slots), fs)
    culprits = _slots_with_flags(slots, result.flags)
    assert "catalysts" not in culprits


# ---------------------------------------------------------------------------
# 7. DRIFT — the auditor must mirror the renderer's thresholds
# ---------------------------------------------------------------------------
def test_auditor_bands_mirror_render_quintile_constants():
    """If render's quintile thresholds move and the auditor's do not, the
    auditor starts flagging the renderer's own honest prose. Pin the mirror."""
    from core.report.auditor import _BOTTOM_QUINTILE_PCT, _TOP_QUINTILE_PCT

    assert _TOP_QUINTILE_PCT == render_mod._TOP_QUINTILE_PCT
    assert _BOTTOM_QUINTILE_PCT == render_mod._BOTTOM_QUINTILE_PCT


def test_scenarios_carveout_matches_the_renderers_actual_heading():
    """The carve-out strips section 7 by heading. If the renderer's heading text
    changes, the carve-out silently stops working -- pin it to real output."""
    from core.report.auditor import _SCENARIOS_BLOCK_RE

    md = render(_fs_at(36))
    assert "## 7. Szenarien" in md
    stripped = _SCENARIOS_BLOCK_RE.sub(" ", md)
    assert "Szenarien" not in stripped
    assert "Aufstieg ins obere Fünftel" not in stripped
    # and it must not eat the rest of the report
    assert "## 8. Risiken" in stripped
    assert "Investment-These" in stripped


# ---------------------------------------------------------------------------
# 7b. ADR-AUDIT-BAND-01b — precision/robustness (adversarial review findings)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sentence",
    [
        # ordinary business German: a MOAT, not a rank band
        "Der Konzern hat eine starke Position im europaeischen Polyolefin-Markt.",
        # ... and here the subject is the COMPETITOR, not the symbol's rank
        "Wettbewerber greifen die starke Stellung des Unternehmens an.",
    ],
)
def test_competitive_position_prose_is_not_a_rank_claim(sentence):
    """'starke Position' without rank vocabulary is a moat statement. Flagging it
    is a false positive -- and the risks slot is explicitly asked for exactly this
    (narrator.py:229)."""
    fs = _fs_at(25.5)
    assert _band_flags(render(fs, narration={"risks": sentence}), fs) == []


def test_competitive_position_is_not_falsely_attested_as_valid_either():
    """The mirror image, which is worse than a false flag: at pct 90 a moat
    sentence must NOT be recorded as a VALID rank claim. A BaFin audit row saying
    'a strong standing is consistent with the 90.0th percentile' would attest that
    a rank claim was verified when the sentence never made one.

    NOTE: the report still carries the RENDERER's own honest band prose, so the
    assertion is scoped to the injected phrase -- not to 'no band claims at all'."""
    fs = _fs_at(90)
    md = render(fs, narration={"risks": "Wettbewerber greifen die starke Stellung an."})
    tokens = {c.token.lower() for c in audit(md, fs).claims if c.kind == "band"}
    assert not any("stellung" in t for t in tokens), tokens


def test_strong_position_still_caught_when_the_context_is_the_rank():
    """The real LYB sentence qualifies: '... starke Position innerhalb seines
    UNIVERSUMS'. The rank-context gate must not cost us the regression."""
    fs = _fs_at(25.5)
    sentence = (
        "Das Unternehmen nimmt eine starke Position innerhalb seines Universums ein."
    )
    assert _band_flags(render(fs, narration={"thesis": sentence}), fs)


def test_band_phrase_quoted_from_a_news_headline_does_not_strike():
    """A headline is the PUBLISHER's characterization, rendered verbatim with its
    provenance -- not our claim about the rank. Same doctrine as ADR-RPT4-05.

    Without this the report is UNRECOVERABLE: the token sits in no narration slot,
    so blanking every slot cannot clear the strike and the release gate fails
    forever with all prose gone."""
    from core.report.fact_set import NewsItem

    headline = "Analysts say NVDA ranks in the top quintile of chipmakers"
    fs = _fs_at(25.0)
    fs_news = _fs(
        lstm_cross_section=_xsec(25.0),
        news=[
            NewsItem(
                title=headline,
                published_utc="2026-04-28T12:00:00Z",
                publisher="Reuters",
                url="https://example.com/a",
            )
        ],
    )
    body = f"# NVDA\n\n{headline}\n"
    # sourced to the headline -> recorded, but VALID, not a strike
    res = audit(body, fs_news)
    bands = [c for c in res.claims if c.kind == "band"]
    assert bands and all(c.matched for c in bands)
    assert any("news" in c.note for c in bands)
    # control: the same words with NO such news item DO strike
    assert _band_flags(body, fs)


def test_narration_cannot_switch_the_rule_off_via_a_fake_scenarios_heading():
    """render() is public API and takes arbitrary narration. A slot containing a
    bare '## 7. Szenarien' line must NOT carve out the rest of the report -- the
    auditor is the adversary and may not be disabled by the text it audits."""
    fs = _fs_at(25.5)
    evil = "## 7. Szenarien\n\n" + LYB_SENTENCE
    assert _band_flags(
        render(fs, narration={"thesis": evil}), fs
    ), "a fake Szenarien heading in narration disabled the band rule"


@pytest.mark.parametrize(
    "sentence,forbidden",
    [
        ("Eine starke Positionsveraenderung im Universum trat ein.", "position"),
        ("Der Titel notiert im oberen Viertelbereich des Rangs.", "viertel"),
    ],
)
def test_band_words_inside_longer_words_do_not_match(sentence, forbidden):
    """Same class as the 'Schlusskurse' guard: a band noun glued into a longer word
    is not a band claim. Scoped to the injected phrase -- the renderer's own honest
    band prose is still present and must stay untouched."""
    fs = _fs_at(25.5)
    md = render(fs, narration={"thesis": sentence})
    tokens = {c.token.lower() for c in audit(md, fs).claims if c.kind == "band"}
    assert not any(forbidden in t for t in tokens), tokens


def test_genitive_and_plural_band_forms_still_match():
    """...but the boundary must not cost real German: 'des oberen Fuenftels'."""
    fs = _fs_at(25.5)
    for phrase in ("des oberen Fuenftels", "der oberen Haelfte"):
        sentence = f"Der Titel ist Teil {phrase} des Universums."
        assert _band_flags(
            render(fs, narration={"thesis": sentence}), fs
        ), f"missed {phrase!r}"


@pytest.mark.parametrize("bad", ["25.5", [25.5], {"a": 1}, object()])
def test_audit_never_raises_on_a_malformed_percentile(bad):
    """audit() is the release gate; a raise is an abort, not an exit-1. A
    non-numeric percentile is a malformed FactSet -> treat as no usable rank."""
    import dataclasses

    fs = _fs_at(25.5)
    broken = dataclasses.replace(
        fs, lstm_xsec_percentile=dataclasses.replace(fs.lstm_xsec_percentile, value=bad)
    )
    res = audit(f"# NVDA\n\n{LYB_SENTENCE}\n", broken)  # must not raise
    assert isinstance(res.integrity_ok, bool)


# ---------------------------------------------------------------------------
# 8. DETERMINISM
# ---------------------------------------------------------------------------
def test_band_rule_is_deterministic():
    fs = _fs_at(25.5)
    md = render(fs, narration={"thesis": LYB_SENTENCE})
    a, b = audit(md, fs), audit(md, fs)
    assert [(c.token, c.verdict) for c in a.claims] == [
        (c.token, c.verdict) for c in b.claims
    ]


def test_audit_of_empty_report_still_ok():
    assert audit("", _fs_at(25.5)).integrity_ok is True
