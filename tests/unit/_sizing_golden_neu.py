"""#4993 — ``sizing_golden_2811.json`` nach der gewollten Aenderung des Pfads "off" neu schreiben.

Nur ein Mensch ruft das auf (``scripts/impl_apply.py``: Netz-Referenzen schreibt kein
Implementierer), und der Diff gehoert begruendet in den PR:

    python tests/unit/_sizing_golden_neu.py [ziel.json]

Gemessen werden ``expected`` und ``trace`` aller Faelle. Dazu kommt je ein Fall mit Vol-Tilt
fuer jeden Fall, in dem bisher ``position_cap`` band: Seit #4993 hebt nur noch ein Tilt ueber
den Deckel, ohne diese Faelle waere er im Set blind (``test_golden_cases_reach_every_cap``).
Ein zweiter Lauf fuegt nichts mehr hinzu.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

_HIER = Path(__file__).resolve().parent
for pfad in (_HIER, _HIER.parents[1]):
    if str(pfad) not in sys.path:
        sys.path.insert(0, str(pfad))

import test_sizing_zero_reason as golden  # noqa: E402


def _mit_tilt(faelle: list) -> list:
    if any("forecast_vol" in f for f in faelle):
        return []
    return [
        {**f, "conv": 1.0, "forecast_vol": 0.01}
        for f in faelle
        if f["trace"].get("binding_limit") == "position_cap"
        and f["trace"].get("sizing_mode") == "conviction"
    ]


def neu_messen(faelle: list) -> list:
    aus = []
    with patch("config.CLEAN_WEIGHT_SIZING", "off", create=True):
        for fall in faelle + _mit_tilt(faelle):
            spur: dict = {}
            wert = golden._size(fall, sink=spur)
            aus.append({**fall, "expected": repr(wert), "trace": spur})
    return aus


if __name__ == "__main__":
    ziel = Path(sys.argv[1]) if len(sys.argv) > 1 else golden.GOLDEN
    ergebnis = neu_messen(golden._golden_cases())
    ziel.write_text(
        json.dumps(ergebnis, indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"geschrieben: {ziel} ({len(ergebnis)} Faelle)")
