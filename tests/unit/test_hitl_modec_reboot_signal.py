"""H2 (#2370): a DB-persisted Mode C (HITL_AUTONOMOUS_UNLIMITED) re-arms unlimited
autonomous LIVE execution on every reboot. The import-time boot gate misses it (it ran
with the env default before the persisted value was applied), so load_and_apply_hitl_policy
must re-emit the Art-14 CRITICAL audit signal on a live boot."""

import asyncio
import logging
from unittest.mock import patch

import config
import core.hitl_policy_store as hps


def _run_with(paper, unlimited, caplog):
    async def fake_load():
        return {"HITL_AUTONOMOUS_UNLIMITED": unlimited}

    with patch.object(hps, "load_hitl_policy", fake_load), patch.object(
        config, "PAPER_TRADING", paper
    ), patch.object(config, "HITL_AUTONOMOUS_UNLIMITED", unlimited), patch.object(
        config, "apply_hitl_policy_update", lambda v: None
    ):
        with caplog.at_level(logging.CRITICAL):
            asyncio.run(hps.load_and_apply_hitl_policy())
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.CRITICAL]


def test_modec_live_reboot_emits_critical(caplog):
    msgs = _run_with(paper=False, unlimited=True, caplog=caplog)
    assert any("Mode C" in m and "LIVE" in m for m in msgs), msgs


def test_modec_paper_reboot_no_critical(caplog):
    msgs = _run_with(paper=True, unlimited=True, caplog=caplog)
    assert not msgs, msgs
