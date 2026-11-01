# language: de
@epic-4983
Funktionalität: [Epic] SIZE-UX — Bemessungsverfahren wählbar, Conviction aus der Round Table, Handelseinstellungen verständlich (#4983)
  e2e: Bemessungsverfahren wählbar, Conviction aus der Round Table, jede Einstellung in ihrer Einheit und mit Wirkung

  Zweck-Kennzahl: Felder der Handelseinstellungen (Advanced-Tabelle und Bemessungsblock), deren Wert ohne seine Einheit angezeigt wird, plus Felder, die im aktiven Bemessungsverfahren keine Wirkung haben und trotzdem bedienbar sind — vorher 17, Ziel 0 (Messung: `python scripts/mess_einstell_ux.py`)

  Diese Abnahme war beim Anlegen rot. Seit #5009 ist sie scharf: Jeder Schritt prüft
  den Code der Sub-Issues von #4983, einen xfail-Marker gibt es nicht mehr.

  Szenario: Das Bemessungsverfahren ist wählbar und sichtbar
  Angenommen die Console-Seite Trading controls mit der Engine im Verfahren "b"
  Dann zeigt der Bemessungsblock "b" als aktives Verfahren
  Wenn ich "Equal weight" wähle und bestätige
  Dann liefert GET /api/trading-settings CLEAN_WEIGHT_SIZING = "a"
  Und die Änderung steht auf der WORM-Audit-Kette

  Szenario: Im Live-Betrieb braucht ein Verfahrenswechsel eine erneute Bestätigung
  Angenommen die Engine handelt live im Verfahren "b"
  Wenn ich "Equal weight" wähle und die Änderung anwende
  Dann verlangt die Console eine erneute Live-Bestätigung, die das alte und das neue Verfahren nennt
  Und ohne Bestätigung bleibt CLEAN_WEIGHT_SIZING = "b"
  Und mit Bestätigung stehen Wechsel und Bestätigung auf der WORM-Audit-Kette
  Und im Paper-Betrieb gilt die normale Bestätigung der Änderung

  Szenario: Kein wirkungsloser Regler wirkt bedienbar
  Angenommen das Verfahren "b"
  Dann sind "Minimum position size" und "Sizing cap" gesperrt und nennen das Verfahren, in dem sie wirken
  Angenommen das Verfahren "a"
  Dann ist zusätzlich "Coverage sizing strength" gesperrt

  Szenario: Der Conviction-Modus rechnet überall mit der Round-Table-Conviction
  Angenommen das Verfahren "off"
  Und eine gehaltene Position mit Round-Table-Conviction 0,70 und LSTM-Vorhersage 0,0
  Wenn ein Nachkauf geprüft und bemessen wird
  Dann rechnen Sizer, Lückendeckel, Totband und Positions-EWMA mit 0,70
  Und das Totband-Ziel ist gleich dem Ziel, das der Sizer anwendet
  Und eine Änderung der LSTM-Vorhersage ändert weder Ziel noch Ordergröße

  Szenario: Die Conviction-Spanne beginnt an der Kaufschwelle
  Angenommen das Verfahren "off", Kaufschwelle 0,60, Minimum 5 %, Sizing cap 30 %, Maximum 25 %
  Wenn ein Kauf mit Conviction 0,60 bemessen wird
  Dann ist das Ziel vor den Risiko-Tilts 5 %
  Wenn ein Kauf mit Conviction 1,0 bemessen wird
  Dann ist das Ziel vor den Risiko-Tilts 25 %

  Szenario: Werte erscheinen in ihrer Einheit
  Angenommen MIN_POSITION_PERCENT = 0.05 und COMPLIANCE_MAX_ORDER_VALUE = 10000
  Dann zeigt die Console "5%" und "$10,000"
  Wenn ich für Minimum position size 4 eingebe und bestätige
  Dann speichert die Engine MIN_POSITION_PERCENT = 0.04

  Szenario: Der Default bleibt unverändert
  Angenommen das Verfahren "b" und unveränderte Einstellungen
  Wenn das Geld-Gate läuft
  Dann sind alle Ordergrößen byte-identisch zum Stand vor dem Epic

  Szenario: Zweck-Kennzahl erreicht
  Wenn python scripts/mess_einstell_ux.py läuft
  Dann meldet es 0
