"""#4874: agent profiles — every statement anchored in the code, docs bound to the source."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from core.agent_profiles import PROFILES, render_markdown
from core.entitlement.tier import _ALL_ROUND_TABLE_AGENTS

pytestmark = [pytest.mark.unit, pytest.mark.vc0]

REPO = Path(__file__).resolve().parents[3]
AGENT_DOCS = REPO / "docs/2_agentic_operations/agents"
START, END = "<!-- profile:start -->", "<!-- profile:end -->"
FORBIDDEN = re.compile(
    r"asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w*", re.I
)
ANCHOR = re.compile(r"^(?P<path>[^:]+):(?P<a>\d+)(?:-(?P<b>\d+))?$")


def _doc_file(name: str) -> Path:
    stem = re.sub(
        r"(?<!^)(?=[A-Z][a-z])", "_", name.replace("VIXAware", "VixAware")
    ).lower()
    return AGENT_DOCS / f"{stem}.md"


def test_every_round_table_agent_has_a_profile():
    assert sorted(PROFILES) == sorted(_ALL_ROUND_TABLE_AGENTS)


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_anchor_points_at_existing_lines(name):
    anchors = list(PROFILES[name]["evidence"]) + [
        s["ref"] for s in PROFILES[name]["settings"]
    ]
    for anchor in anchors:
        m = ANCHOR.match(anchor)
        assert m, f"{name}: malformed anchor {anchor!r}"
        path = REPO / m["path"]
        assert path.is_file(), f"{name}: {anchor} — file missing"
        n_lines = len(path.read_text(encoding="utf-8").splitlines())
        last = int(m["b"] or m["a"])
        assert (
            int(m["a"]) <= last <= n_lines
        ), f"{name}: {anchor} beyond {n_lines} lines"


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_settings_defaults_equal_the_shipped_literal(name):
    for s in PROFILES[name]["settings"]:
        m = ANCHOR.match(s["ref"])
        lines = (REPO / m["path"]).read_text(encoding="utf-8").splitlines()
        window = "\n".join(lines[int(m["a"]) - 1 : int(m["a"]) + 3])
        assert s["key"] in window, f"{name}: {s['key']} not at {s['ref']}"
        assert f'os.getenv("{s["key"]}", "{s["default"]}"' in window or (
            f'"{s["key"]}", "{s["default"]}"' in window
        ), f"{name}: default {s['default']!r} for {s['key']} not at {s['ref']}"


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_measured_values_name_a_committed_artefact_or_say_not_measured(name):
    for line in PROFILES[name]["measured"]:
        assert line.startswith("Not measured") or "docs/rtr/" in line, line
        for artefact in re.findall(r"docs/rtr/\S+?\.md", line):
            assert (REPO / artefact).is_file(), artefact


def test_profiles_pass_the_wording_guard():
    own_words = {
        n: {k: v for k, v in p.items() if k != "literature"}
        for n, p in PROFILES.items()
    }
    assert not FORBIDDEN.findall(json.dumps(own_words, ensure_ascii=False))


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_agent_page_shows_exactly_the_profile(name):
    text = _doc_file(name).read_text(encoding="utf-8")
    assert (
        START in text and END in text
    ), f"{_doc_file(name).name}: run scripts/gen_agent_profiles.py"
    section = text.split(START, 1)[1].split(END, 1)[0].strip()
    assert section == render_markdown(name)


def test_agents_view_serves_the_profile():
    from core.trading_settings import agents_view

    agents = {a["name"]: a for a in agents_view()["agents"]}
    assert agents["VIXAwareRiskAgent"]["profile"]["type"] == "Deterministic"
    assert agents["SpecialistAlphaAgent"]["profile"]["type"] == "AI language model"
    assert agents["SkewSizeTilt"]["profile"] == {}
