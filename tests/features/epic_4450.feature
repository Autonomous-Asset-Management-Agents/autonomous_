# language: de
@epic-4450
Funktionalität: [Epic] Strategische Neupositionierung: Von Asset Management zu Execution-Framework (#4450)
  e2e: Landing-Copy ohne Asset-Management-, Rendite- und Sicherheitsversprechen, indexierbar, mit llms.txt, cookieless Messung und UX Judge

  Zweck-Kennzahl: Treffer der Begriffe asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w* (ohne Groß-/Kleinschreibung) in der öffentlichen Landing-Copy: String-Werte von src/locales/en|de/translation.json + JSX-Textknoten von src/components/views/LandingViewE.tsx + index.html ohne Kommentare — vorher 38 (gemessen 10.10.2026 auf main 2e0558531: en 16, de 18, LandingViewE 4, index.html 0), Ziel 0 (Messung: `python -c "import json,re,pathlib as P;R=re.compile(r'asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w*',re.I);W=lambda o:[s for v in (o.values() if isinstance(o,dict) else o if isinstance(o,list) else []) for s in W(v)] if not isinstance(o,str) else [o];n=sum(len(R.findall(s)) for l in ('en','de') for s in W(json.loads(P.Path(f'src/locales/{l}/translation.json').read_text(encoding='utf-8'))));n+=sum(len(R.findall(s)) for s in re.findall(r'>([^<>{}]+)<',P.Path('src/components/views/LandingViewE.tsx').read_text(encoding='utf-8')));n+=len(R.findall(re.sub(r'<!--.*?-->','',P.Path('index.html').read_text(encoding='utf-8'),flags=re.S)));print(n)"`)

  Diese Abnahme ist beim Anlegen rot und bleibt es, solange das Epic offen ist.
  Sie wird grün, wenn alle Sub-Issues von #4450 umgesetzt sind — und
  erst dann darf der xfail-Marker in den Schrittdefinitionen fallen.

  Szenario: Die öffentliche Website positioniert AAAgents als Execution-Framework und ist messbar
  Angenommen der Stand von main
  Wenn die Zweck-Kennzahl gezählt wird (Treffer von asset management, profitab*, safe, sicher, rendite* in den String-Werten von src/locales/en|de/translation.json, den JSX-Textknoten von src/components/views/LandingViewE.tsx und index.html außerhalb von Kommentaren)
  Dann ist sie 0
  Und index.html enthält außerhalb von Kommentaren kein robots-Meta "noindex"
  Und index.html enthält einen parsebaren JSON-LD-Block mit "@type" "SoftwareApplication"
  Und public/llms.txt existiert, beginnt mit "# " und nennt "execution framework"
  Und src/lib/analytics.ts existiert und index.html lädt kein Skript von googletagmanager.com
  Und scripts/ux_judge.py existiert und docs/ux_personas/ enthält mindestens drei Personas
