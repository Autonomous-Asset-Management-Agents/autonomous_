# language: de
@epic-4638
Funktionalität: [Epic] Round Table & Iron Dome als QA-Gate für LLM- & Prompt-basierte Strategien (LLM-Abfrage als Vorauswahl statt/neben LSTM) (#4638)
  e2e: Die gespeicherte LLM-Abfrage liefert die Vorauswahl für den Round Table, jeder Vorschlag erscheint mit Endstatus im QA-Trichter, und kein halluzinierter Ticker gelangt in die Tiefenbewertung

  Zweck-Kennzahl: Anteil der gültigen LLM-Vorschläge (im aktiven Universum), die im Simulationszyklus in die Round-Table-Tiefenbewertung gelangen UND im QA-Trichter mit Endstatus ausgewiesen sind; dazu die Zahl erfundener Ticker in der Tiefenbewertung — vorher 0 % — grep liefert am 22.10.2026 0 Dateien: Es gibt keine LLM-Ranking-Quelle; die einzige Ranking-Quelle des Trichters ist active_cross_section aus core/report/lstm_panel_store.py (zyklus_kontext.py:270), Ziel 100 % der gültigen LLM-Vorschläge in Tiefenbewertung und Trichter, 0 erfundene Ticker in der Tiefenbewertung (Messung: `python -m pytest ai_trading_bot/tests/e2e/test_llm_vorauswahl_qa_gate_4638.py -q -k zweck (gibt den Anteil aus). Vorher-Messung, da es weder Quelle noch Test gibt: grep -rlE 'llm_ranking|LLM_RANKING|llm_vorauswahl' ai_trading_bot/core src | wc -l`)

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #4638 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Szenario: Die LLM-Vorauswahl speist den Rang-Trichter und Round Table und Iron Dome prüfen jeden Vorschlag
  Angenommen eine Strategie mit der Ranking-Quelle 'llm', einem gespeicherten Prompt und dem Rhythmus 'je Ranking-Refresh'
  Und ein Stub-Provider an der LLM-Seam core/llm/provider.py liefert 30 Ticker, davon 3 erfunden und 2 nicht im aktiven Universum
  Und das Depot hält 2 Positionen außerhalb der LLM-Liste
  Wenn ein Simulationszyklus mit AAA_SIM_MODE=true läuft
  Dann enthält der Deep-Eval-Satz (z.symbols_order nach _kontext_rang_trichter) genau die 2 gehaltenen Positionen und die 25 gültigen LLM-Ticker
  Und kein erfundener und kein universumsfremder Ticker steht im Deep-Eval-Satz
  Und das Lauf-Protokoll enthält Zeitpunkt, Prompt-Hash, Modell, Anbieter, 25 gelieferte und 5 verworfene Ticker mit Grund
  Und der QA-Trichter-Endpunkt weist alle 30 Vorschläge mit Endstatus aus: verworfen vor dem Funnel, abgelehnt am Round Table, abgelehnt am Iron Dome mit Grund oder zugelassen
  Und die Zweck-Kennzahl 'Anteil gültiger LLM-Vorschläge in Tiefenbewertung und Trichter' ist 100 %
  Und das Modul der LLM-Ranking-Quelle importiert weder order_executor noch SignalCandidate

  Szenario: Fällt das LLM aus, gilt die letzte Liste weiter und danach das LSTM-Ranking
  Angenommen ein erfolgreicher Lauf liegt 1 Stunde zurück und die Höchstdauer beträgt 4 Stunden
  Wenn der nächste Lauf mit Timeout scheitert
  Dann bleibt die Liste des letzten erfolgreichen Laufs die Vorauswahl
  Und nach Überschreiten der Höchstdauer liest der Trichter das LSTM-Ranking oder das ganze Universum
  Und eine WARNING steht im Log und gehaltene Positionen bleiben in der Bewertung
