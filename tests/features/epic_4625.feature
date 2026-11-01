# language: de
@epic-4625
Funktionalität: [Epic] STW — Strategie-Werkstatt: eigene Strategien simulieren, vergleichen und live stellen (#4625)
  e2e: Bis zu drei eigene Strategien werden versiegelt angelegt, per Simulation verglichen, parallel auf Paper-Konten betrieben und WORM-versiegelt dem Live-Konto zugewiesen — neutral nach ADR-027

  Angelegt in #4626, scharf geschaltet in #5026 und #5044. Jede Probe fährt den Code
  ihres Sub-Issues.

  # Geprüft auf der echten Kette — #4629
  Szenario: Strategie anlegen wird versiegelt
    Angenommen ein Nutzer legt die Strategie "A" mit eigenen Settings an
    Dann steht ein StrategyDefinitionEvent auf der Audit-Kette
    Und die Kette verifiziert

  # Pipeline und Simulation sind echt (#4634, #3712, #4635); die Order-Probe fordert gefüllte Orders — #5044
  Szenario: Drei Strategien im Sim vergleichen
    Angenommen drei Strategien und ein Zeitraum innerhalb der letzten drei Jahre
    Wenn der Nutzer die Simulation startet
    Dann erhält jede Strategie Rendite (TWR), maximalen Drawdown und SPY-Vergleich für genau diesen Zeitraum
    Und zwei Läufe mit gleicher Eingabe sind bitgleich
    Und jedes Ergebnis zeigt Pflichthinweis, Zeitraum, Unsicherheit und nicht abgebildete Teile

  # Route, Kette und Engine-Start im Kindprozess — #4629, #4630
  Szenario: Live-Strategie zuweisen
    Wenn der Nutzer in den Settings Strategie "B" dem Live-Konto zuweist
    Dann steht ein StrategyAssignmentEvent vor dem Wirksamwerden auf der Kette
    Und die Engine startet mit den Settings von "B" und aktivem HITL

  # Drei Engine-Kindprozesse mit echter Sperre je Konto-ID — #4631, #4632
  Szenario: Paper-Konten laufen parallel
    Angenommen drei Paper-Profile mit je eigenem Alpaca-Konto
    Dann laufen drei Engines nebeneinander
    Und ein weiteres Profil mit derselben Konto-ID wird abgewiesen

  # Route in der Engine, Ansicht über die Vitest-Brücke — #4633
  Szenario: Vergleich im Betrieb
    Dann zeigt die Vergleichsansicht die Rendite (TWR) je Konto für Live und Paper nebeneinander

  # /set-strategy auf der echten Kette (#4629), Ansichten über die Vitest-Brücke (#4635, #4636)
  Szenario: Neutrale Darstellung
    Dann enthält keine Ansicht der Werkstatt eine Rangfolge, "best", "recommended" oder einen Übernehmen-Knopf
    Und /set-strategy schaltet nicht mehr unauditiert um

  # Bedienfluss über die Vitest-Brücke — #4636
  Szenario: Bedienfluss folgt dem UX-Konzept
    Angenommen das abgenommene UX-Konzept
    Dann sind die Abläufe Anlegen, Simulieren, Vergleichen und Zuweisen im Frontend-Integrationstest durchgängig bedienbar
    Und jeder im Konzept genannte Zustand (leer, lädt, Fehler, keine Keys, Konto doppelt) ist abgebildet

  # Pipeline und Simulation sind echt; Enthaltungen nach Regel bleiben Enthaltungen — #5083
  Szenario: Die Simulation rechnet den Round Table wie live
    Angenommen ein Korpus aus der Datenpipeline
    Wenn die Strategie-Simulation zwei Strategien vergleicht, die sich nur im Gewicht eines abgebildeten Agenten außer Momentum und Trend unterscheiden
    Dann trägt jeder Handelstag die Stimmen aller abgebildeten Agenten
    Und die Simulation erzeugt Orders
    Und die beiden Ergebniskurven unterscheiden sich
