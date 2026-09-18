"""#3132 — Portfoliostruktur einstellbar: Grenzen, Aenderungsliste, WORM-Ereignis.

Drei Invarianten, die nicht verhandelbar sind:

1. **Grenzen serverseitig.** Die Oberflaeche ist Bequemlichkeit, nicht die Absicherung.
   N > 20 waere ein Slot < MIN_POSITION_PERCENT (0,05) — das Buch koennte sich nicht
   fuellen (POSITION_BOOK_CAP_TUNING.md).

2. **Nur Text im Audit-Eintrag.** ``LiveEnableEvent`` haelt ausdruecklich fest, dass alle
   Felder ``str`` sein muessen: die SHA-256-Vorlage muss byte-identisch sein, wenn der
   JS-Verifizierer (``audit-chain.cjs``) sie nachrechnet. Ein Float kann zwischen
   ``json.dumps`` und ``JSON.stringify`` abweichen und wuerde den Live-Handel dauerhaft
   fail-closed setzen.

3. **Keine Aenderung, kein Eintrag.** Ein Aufruf, der nichts veraendert, darf die Kette
   nicht mit Rauschen fuellen.
"""

import pytest

from core.portfolio_shape import (
    HOLD_DAYS_BOUNDS,
    POSITIONS_BOUNDS,
    VOL_TARGET_BOUNDS,
    clamp_portfolio_shape,
    describe_changes,
)


class TestGrenzen:
    def test_defaults_bleiben_der_heutige_stand(self):
        got = clamp_portfolio_shape(positions=10, hold_days=20, vol_target=0.015)
        assert got == {"positions": 10, "hold_days": 20.0, "vol_target": 0.015}

    @pytest.mark.parametrize(
        "roh,erwartet",
        [(25, 20), (21, 20), (20, 20), (10, 10), (9, 10), (0, 10), (-5, 10)],
    )
    def test_positionen_klemmen_auf_10_bis_20(self, roh, erwartet):
        """25 ist die Anforderung, 20 die Mechanik: Slot 1/25 = 4 % < 5 % Minimum."""
        assert clamp_portfolio_shape(positions=roh)["positions"] == erwartet

    @pytest.mark.parametrize(
        "roh,erwartet", [(21, 20), (20, 20), (5, 5), (4, 5), (0, 5), (-1, 5)]
    )
    def test_haltedauer_klemmt_auf_5_bis_20(self, roh, erwartet):
        assert clamp_portfolio_shape(hold_days=roh)["hold_days"] == erwartet

    @pytest.mark.parametrize(
        "roh,erwartet", [(0.06, 0.05), (0.05, 0.05), (0.001, 0.001), (0.0001, 0.001)]
    )
    def test_vol_ziel_klemmt_auf_die_bestehenden_grenzen(self, roh, erwartet):
        assert clamp_portfolio_shape(vol_target=roh)["vol_target"] == erwartet

    def test_positionen_sind_ganzzahlig(self):
        assert clamp_portfolio_shape(positions=14.7)["positions"] == 14

    @pytest.mark.parametrize(
        "mist", [None, "zwanzig", float("nan"), float("inf"), True]
    )
    def test_unbrauchbare_eingaben_fallen_auf_den_default(self, mist):
        """Fail-safe: nie raten, nie werfen — der heutige Wert gilt weiter."""
        assert clamp_portfolio_shape(positions=mist)["positions"] == 10

    def test_die_grenzen_sind_die_dokumentierten(self):
        assert POSITIONS_BOUNDS == (10, 20)
        assert HOLD_DAYS_BOUNDS == (5.0, 20.0)
        assert VOL_TARGET_BOUNDS == (0.001, 0.05)


class TestAenderungsliste:
    def test_nur_veraenderte_werte_erscheinen(self):
        changes = describe_changes(
            current={"positions": 10, "hold_days": 20.0, "vol_target": 0.015},
            new={"positions": 20, "hold_days": 10.0, "vol_target": 0.015},
        )
        assert [c["key"] for c in changes] == [
            "FULL_UNIVERSE_MAX_POSITIONS",
            "SMART_EXIT_MIN_HOLD_DAYS",
        ]

    def test_ohne_aenderung_bleibt_die_liste_leer(self):
        same = {"positions": 10, "hold_days": 20.0, "vol_target": 0.015}
        assert describe_changes(current=same, new=dict(same)) == []

    def test_jedes_feld_ist_text(self):
        """Kritisch: Floats koennen die SHA-256-Vorlage zwischen Python und JS
        auseinanderlaufen lassen und den Live-Handel dauerhaft fail-closed setzen."""
        changes = describe_changes(
            current={"positions": 10, "hold_days": 20.0, "vol_target": 0.015},
            new={"positions": 20, "hold_days": 10.0, "vol_target": 0.02},
        )
        assert changes, "es muss Aenderungen geben, sonst prueft der Test nichts"
        for c in changes:
            for feld, wert in c.items():
                assert isinstance(wert, str), f"{feld}={wert!r} ist kein str"

    def test_die_engine_flag_namen_werden_verwendet(self):
        """setup.json wird unter den EXAKTEN Engine-Flag-Namen geschrieben (#2863)."""
        changes = describe_changes(
            current={"positions": 10, "hold_days": 20.0, "vol_target": 0.015},
            new={"positions": 12, "hold_days": 8.0, "vol_target": 0.02},
        )
        assert {c["key"] for c in changes} == {
            "FULL_UNIVERSE_MAX_POSITIONS",
            "SMART_EXIT_MIN_HOLD_DAYS",
            "VOL_TARGET_DAILY_VOL",
        }


class TestWormEreignis:
    def test_ereignis_traegt_ausschliesslich_text(self):
        from core.round_table.senate_log import PortfolioShapeEvent

        ev = PortfolioShapeEvent(
            timestamp="2026-08-31T09:14:22+00:00",
            actor="operator",
            acknowledgment="bestaetigt",
            nonce="9f2c",
            changes='[{"key": "FULL_UNIVERSE_MAX_POSITIONS", "from": "10", "to": "20"}]',
            switch_id="9f2c",
        )
        for feld, wert in vars(ev).items():
            assert isinstance(wert, str), f"{feld} ist kein str"

    @pytest.mark.asyncio
    async def test_strikt_reicht_einen_schreibfehler_durch(self, monkeypatch):
        """Audit-vor-Aenderung: schlaegt die WORM-Schreibung fehl, darf die API
        keinen Erfolg melden — sonst gaebe es eine Aenderung ohne Eintrag."""
        from unittest.mock import AsyncMock

        from core import hitl_gate

        mock_logger = AsyncMock()
        mock_logger.log_hitl_event.side_effect = RuntimeError(
            "Kette nicht beschreibbar"
        )

        monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: mock_logger)
        with pytest.raises(RuntimeError):
            await hitl_gate.log_portfolio_shape_event(
                changes=[
                    {"key": "FULL_UNIVERSE_MAX_POSITIONS", "from": "10", "to": "20"}
                ],
                acknowledgment="bestaetigt",
                nonce="9f2c",
                strict=True,
            )

    @pytest.mark.asyncio
    async def test_nicht_strikt_schluckt_den_fehler(self, monkeypatch):
        from unittest.mock import AsyncMock

        from core import hitl_gate

        mock_logger = AsyncMock()
        mock_logger.log_hitl_event.side_effect = RuntimeError(
            "Kette nicht beschreibbar"
        )

        monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: mock_logger)
        await hitl_gate.log_portfolio_shape_event(
            changes=[{"key": "FULL_UNIVERSE_MAX_POSITIONS", "from": "10", "to": "20"}],
            acknowledgment="bestaetigt",
            nonce="9f2c",
            strict=False,
        )

    @pytest.mark.asyncio
    async def test_geschriebenes_ereignis_enthaelt_die_aenderungen(self, monkeypatch):
        from unittest.mock import AsyncMock

        from core import hitl_gate

        gesehen = []

        mock_logger = AsyncMock()

        async def side_effect(ev):
            gesehen.append(ev)

        mock_logger.log_hitl_event.side_effect = side_effect

        monkeypatch.setattr(hitl_gate, "_resolve_audit_logger", lambda: mock_logger)
        await hitl_gate.log_portfolio_shape_event(
            changes=[{"key": "SMART_EXIT_MIN_HOLD_DAYS", "from": "20", "to": "10"}],
            acknowledgment="bestaetigt",
            nonce="abcd",
        )
        assert len(gesehen) == 1
        ev = gesehen[0]
        assert "SMART_EXIT_MIN_HOLD_DAYS" in ev.changes
        assert ev.nonce == "abcd"
        assert (
            ev.switch_id == "abcd"
        ), "ohne eigene Angabe korreliert der Eintrag mit sich selbst"
