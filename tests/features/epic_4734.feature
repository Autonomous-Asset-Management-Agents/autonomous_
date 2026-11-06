# language: de
@epic-4734
Funktionalität: [Epic] FAB-GOV — Der Automat ändert Workflows, wenn er es beweisen kann (Vorlage-Übernahme + Invariantentor statt Blanko-Sperre) (#4734)
  e2e: Der Implementierer darf die fünf freigegebenen Workflow-Vorlagen übernehmen, und das Invariantentor ist rot für 71928a4545c8a

  Zweck-Kennzahl: Anzahl der freigegebenen Workflow-Vorlagen auf main (#4697, #4699, #4701, #4703, #4764 — je einzeln auf origin/main angewandt), für die der Implementierer einen PR einreichen darf, bei gleichzeitig rotem Invariantentor für den Diff von 71928a4545c8a — vorher 0 von 5 (gemessen 24.10.2026 auf main @ f36871763; jeder Diff scheitert an impl_apply.verbotene_pfade, scripts/impl_apply.py:197 / Abbruch :445). Invariantentor: existiert nicht (scripts/workflow_invarianten.py fehlt), Befund für 71928a4545c8a also nicht erhebbar, Ziel 5 von 5 zulässig UND workflow_invarianten.py meldet für 71928a4545c8a Exit 1 UND für origin/main gegen origin/main Exit 0 (Messung: `python -c "import sys; sys.path.insert(0,'scripts'); import impl_apply; z={'4697':['.github/workflows/ci.yml'],'4699':['.github/workflows/ci.yml'],'4701':['.github/workflows/ci.yml'],'4703':['.github/workflows/oss-ci.yml'],'4764':['.github/workflows/cia-gate.yml','.github/workflows/governance-guard.yml','.github/workflows/plan-gate.yml','.github/workflows/tor-neulauf.yml']}; print(sum(1 for v in z.values() if not impl_apply.verbotene_pfade(v)),'von',len(z))"  — nach dem Epic: pytest scripts/tests/test_fab_gov_e2e.py -k zweck (zählt dieselben fünf über den Beweispfad)`)

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #4734 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Szenario: Beweis statt Blanko-Sperre — Vorlage-Übernahme zulässig, historische Aufweichung rot
  Angenommen origin/main mit den Workflow-Vorlagen aus #4697, #4699, #4701, #4703 und #4764 unter docs/<nr>-<slug>/
  Und der Commit 71928a4545c8a (pr-size-enforcer.yml: threshold 1000 → 10000)
  Wenn `python scripts/workflow_invarianten.py --basis 71928a4545c8a~1 --ziel 71928a4545c8a` läuft
  Dann endet es mit Exit 1 und einem Befund, der pr-size-enforcer.yml und 1000 → 10000 nennt
  Wenn `python scripts/workflow_invarianten.py --basis origin/main --ziel origin/main` läuft
  Dann endet es mit Exit 0 und ohne Befund
  Wenn jede der fünf Vorlagen einzeln mit `vorlagen_beweis.wende_an` auf origin/main angewandt wird
  Und impl_apply über den so entstandenen Diff entscheidet (Vertragszeile `Vorlage-Beweis:` gesetzt)
  Dann lässt impl_apply für 5 von 5 Vorlagen einen PR zu (Zweck-Kennzahl, heute 0 von 5)
  Wenn eine der Vorlagen angewandt und zusätzlich eine Zeile frei geändert wird
  Dann bricht impl_apply mit Grund `governance` ab, und es entsteht kein PR
  Und governance-guard.yml sowie die Vorlage docs/4764-…/governance-guard.yml rufen den Beweis in der Fassung des Basis-Branches auf
  # Heute rot: scripts/workflow_invarianten.py und scripts/vorlagen_beweis.py fehlen (ImportError, kein importorskip)
