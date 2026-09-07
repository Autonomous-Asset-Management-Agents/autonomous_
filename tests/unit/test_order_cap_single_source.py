# tests/unit/test_order_cap_single_source.py
"""#3218 (A2) — es gibt genau EINE Quelle fuer den Order-Wert-Deckel.

Die Inventur vom 04.09.2026 fand fuenf Definitionen derselben Groesse, von
denen nur zwei wirken:

- D1 ``COMPLIANCE_MAX_ORDER_VALUE`` (Config/env) — bindet
- D2 ``STRICT_DEFAULT.max_order_value`` (Iron-Dome-Policy) — bindet ab ``/start``
- D3 ``MAX_ORDER_VALUE_CEILING`` — ratifizierte Obergrenze, klemmt D2
- D4 ``Entitlement.max_order_value`` je Tier — **toter Code**, kein
  Durchsetzungspfad liest das Feld; es sieht aber wie eine Produktregel aus
- D5 ``.env.oss.example`` mit 50.000 — **widerspricht** dem Code-Default 10.000

D4 und D5 werden hier dauerhaft ausgeschlossen. In einem auditierten System
ist eine Kontrolle, die aussieht wie eine Regel und keine ist, eine
Haftungsflaeche — und eine Beispieldatei, die den fuenffachen Deckel setzt,
eine Falle fuer jeden, der sie wie vorgesehen kopiert.
"""

import io
import os
from dataclasses import fields

import allure

from core.entitlement.tier import Entitlement

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


@allure.feature("VC-4 Risk Management & Compliance")
@allure.story("Risk & Compliance")
class TestOrderCapSingleSource:
    def test_r1_no_tier_level_order_cap(self):
        """D4: kein Entitlement-Tier traegt einen eigenen Orderwert-Deckel.

        Wird das Feld je wieder gebraucht, ist der richtige Ort die
        Iron-Dome-Policy je Installation — nicht eine zweite parallele Quelle,
        die kein Durchsetzungspfad liest.
        """
        names = {f.name for f in fields(Entitlement)}
        assert "max_order_value" not in names, (
            "Entitlement traegt wieder einen Order-Deckel. Kein Durchsetzungspfad "
            "liest ihn — er waere eine zweite, stille Quelle neben der Policy."
        )

    def test_r2_env_example_matches_the_code_default(self):
        """D5: die Beispiel-Umgebung darf dem Code-Default nicht widersprechen."""
        from config import COMPLIANCE_MAX_ORDER_VALUE

        path = os.path.join(_REPO_ROOT, ".env.oss.example")
        assert os.path.exists(path), f"{path} fehlt"

        declared = None
        for line in io.open(path, encoding="utf-8"):
            stripped = line.strip()
            if stripped.startswith("COMPLIANCE_MAX_ORDER_VALUE="):
                declared = float(stripped.split("=", 1)[1].strip())
                break

        assert (
            declared is not None
        ), "COMPLIANCE_MAX_ORDER_VALUE fehlt in .env.oss.example"
        assert declared == float(COMPLIANCE_MAX_ORDER_VALUE), (
            f".env.oss.example nennt {declared}, der Code-Default ist "
            f"{COMPLIANCE_MAX_ORDER_VALUE} — wer die Datei kopiert, betriebe das "
            f"System unbemerkt mit einem anderen Deckel."
        )
