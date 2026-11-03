"""#4612 — das gebuendelte Abbild wird am INHALT geprueft, nicht an der Anzahl.

Der bisherige Waechter war ``assert snap and len(snap) > 400``. Gemessen am 21.10.2026
hat er damit ein Abbild durchgelassen, das von einem Erzeuger **vor** dem
Jahresdauer-Filter und **vor** der Sortierung aus #4567 stammte:

============================================  =========  ========
gemessen am ausgelieferten Abbild               alt        neu
============================================  =========  ========
Symbole                                            476       500
Perioden insgesamt                              20.837     9.091
Median Perioden je Titel                            51        19
neueste-Periode-zuerst sortiert                0 von 476  500/500
juengste Periode ohne Umsatz (Agentenpfad)          47         8
juengste Periode ohne Gewinn                        35         1
============================================  =========  ========

Am Verbraucherpfad war die Folge messbar: fuer **URI** waehlte ``derive_fundamentals``
die Periode ``2025-03-31`` — ein **Quartal** — als aktuelle Kennzahl, mit Umsatz ``None``
und 518 Mio Gewinn (dem eines Quartals). Und 47 Titel lieferten keine Marge, worauf der
FundamentalsAgent sich mit ``no verified fundamentals data yet`` enthielt: das sieht wie
eine Datenluecke aus und war ein veraltetes Abbild.

``pruefe_abbild`` ist bewusst **eine** Stelle fuer beide Nutzer — diese Tests und das Tor
im Neubau-Workflow, das einen PR verweigert, wenn das frische Abbild schlechter waere als
das, das es ersetzen soll.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from core.report import financials_snapshot as fs
from core.report.financials_feed import derive_fundamentals


def _zeile(end, rev, ni, filed=None):
    return {
        "end_date": end,
        "filing_date": filed or f"{int(end[:4]) + 1}-02-01",
        "financials": {
            "income_statement": {
                "revenues": {"value": rev},
                "net_income_loss": {"value": ni},
            }
        },
    }


def _abbild(n=500, **sonder):
    """Ein tragfaehiges Abbild mit ``n`` Titeln; ``sonder`` ueberschreibt einzelne."""
    out = {
        f"SYM{i}": {
            "results": [_zeile("2025-12-31", 100, 10), _zeile("2024-12-31", 90, 9)]
        }
        for i in range(n)
    }
    out.update(sonder)
    return out


# ── Das ausgelieferte Abbild ─────────────────────────────────────────────────


@pytest.mark.vc1
def test_ausgeliefertes_abbild_ist_inhaltlich_tragfaehig():
    """Der Kern dieses Tickets: Anzahl allein genuegt nicht mehr."""
    snap = fs.load_snapshot(fs.DEFAULT_SNAPSHOT_PATH)
    befunde = fs.pruefe_abbild(snap)
    assert befunde == [], "\n".join(befunde)


@pytest.mark.vc1
@pytest.mark.parametrize(
    "symbol,ende,umsatz",
    [
        # Genau die Titel, an denen die drei Tickets haengen — im alten Abbild trug
        # jeder von ihnen `None`.
        ("URI", "2025-12-31", 16_099_000_000),  # #4570
        ("NEE", "2025-12-31", 27_412_000_000),  # #4570 (Groessenregel)
        ("XEL", "2025-12-31", 14_669_000_000),  # #4567 (Klasse B)
        ("CVS", "2025-12-31", 402_067_000_000),  # #4567 (Klasse A)
    ],
)
def test_die_drei_tickets_erreichen_eine_frische_installation(symbol, ende, umsatz):
    """#4567, #4570 und #4571 werden fuer eine Neuinstallation erst hiermit wirksam.

    Vorher las der erste Zyklus einer frischen Installation das alte Abbild: URI mit
    der Quartalsperiode ``2025-03-31``, XEL/NEE/CVS mit Umsatz ``None``.
    """
    snap = fs.load_snapshot(fs.DEFAULT_SNAPSHOT_PATH)
    assert symbol in snap, f"{symbol} fehlt im ausgelieferten Abbild"
    d = derive_fundamentals(
        snap[symbol]["results"], as_of=date(2026, 10, 21), close=None
    )
    assert d is not None, f"{symbol}: kein Ergebnis"
    assert d["end_date"] == ende, f"{symbol}: Periode {d['end_date']} statt {ende}"
    assert d["revenue"] == umsatz, f"{symbol}: Umsatz {d['revenue']!r} statt {umsatz}"
    assert (
        d["net_margin"] is not None
    ), f"{symbol}: keine Marge — der Agent enthaelt sich"


@pytest.mark.vc1
def test_das_abbild_bleibt_klein_genug_zum_mitliefern():
    """Es fuhr schon mit 832 KB mit; nach dem Neubau sind es 589 KB. Eine Obergrenze
    haelt fest, dass der Desktop-Bau nicht unbemerkt um Megabytes waechst."""
    import os

    bytes_ = os.path.getsize(fs.DEFAULT_SNAPSHOT_PATH)
    assert bytes_ < 2_000_000, f"{bytes_} Bytes — das Abbild waechst unbemerkt"


# ── Die Pruefung selbst ──────────────────────────────────────────────────────


@pytest.mark.vc1
def test_anzahl_allein_genuegt_nicht_mehr():
    """Szenario des Tickets: ueber 400 Symbole, aber `revenues` None bei einem Titel."""
    snap = _abbild(500, KAPUTT={"results": [_zeile("2025-12-31", None, 10)]})
    befunde = fs.pruefe_abbild(snap)
    assert befunde, "ein Titel ohne Umsatz muss auffallen"
    assert any("ohne Umsatz" in b for b in befunde), befunde


@pytest.mark.vc1
def test_bekannte_luecken_sind_eine_ratsche():
    """Die acht gemessenen Luecken sind benannt und begruendet — ein NEUNTER faellt auf."""
    assert "TFC" in fs.ABBILD_BEKANNTE_LUECKEN, "die gemessenen Luecken fehlen"
    snap = _abbild(500, TFC={"results": [_zeile("2025-12-31", None, 10)]})
    assert fs.pruefe_abbild(snap) == [], "eine benannte Luecke darf nicht schlagen"
    snap["NEUERFALL"] = {"results": [_zeile("2025-12-31", None, 10)]}
    assert fs.pruefe_abbild(snap), "eine NEUE Luecke muss schlagen"


@pytest.mark.vc1
def test_unsortierte_perioden_fallen_auf():
    """Im alten Abbild war KEIN einziger Titel sortiert — `_pit_select` waehlt dann bei
    gleichem Einreichungsdatum die falsche Periode."""
    snap = _abbild(
        500,
        DREHT={"results": [_zeile("2020-12-31", 50, 5), _zeile("2025-12-31", 100, 10)]},
    )
    befunde = fs.pruefe_abbild(snap)
    assert any("sortiert" in b for b in befunde), befunde


@pytest.mark.vc1
def test_quartalsdauern_fallen_ueber_den_median_auf():
    """Das alte Abbild trug Median 51 Perioden je Titel statt 19."""
    viele = {
        f"Q{i}": {
            "results": [
                _zeile(f"{2025 - (j // 4)}-{[3, 6, 9, 12][j % 4]:02d}-30", 10, 1)
                for j in range(60)
            ]
        }
        for i in range(500)
    }
    befunde = fs.pruefe_abbild(viele)
    assert any("Median" in b for b in befunde), befunde


@pytest.mark.vc1
def test_zu_wenige_symbole_fallen_auf():
    """476 war der Stand des alten Abbilds; die Live-Indexliste hat ueber 500."""
    befunde = fs.pruefe_abbild(_abbild(476))
    assert any("Symbole" in b for b in befunde), befunde


@pytest.mark.vc1
def test_leeres_abbild_ist_ein_befund_und_kein_absturz():
    assert fs.pruefe_abbild(None)
    assert fs.pruefe_abbild({})


@pytest.mark.vc1
def test_pruefung_ist_json_serialisierbar():
    """Das Workflow-Tor gibt die Befunde in die Lauf-Zusammenfassung — sie muessen
    ohne Weiterverarbeitung ausgebbar sein."""
    befunde = fs.pruefe_abbild(_abbild(10))
    assert json.dumps(befunde)
