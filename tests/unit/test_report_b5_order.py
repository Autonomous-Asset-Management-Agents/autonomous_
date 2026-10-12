# tests/unit/test_report_b5_order.py
# B5 — Georgs diktierte Reihenfolge, durchgehende Nummerierung, und das
# Ehrlichkeits-Label an der Nachrichtenlage.
#
# Georg, wörtlich:
#   "das ist die firma / das macht die / das sagen unsere ml modelle dazu /
#    das sind news artiekel von jz grad / so könnten die beiden dinge
#    zusammenhängen / das hier sind unsere theorien zu was mit der aktie
#    passieren wird warum sie so steht und ob man sie kaufen sollte oder nicht"
#
# Vier Dinge werden hier festgenagelt:
#   1. Die REIHENFOLGE der Abschnitte (Georgs Diktat).
#   2. Die NUMMERIERUNG läuft in Lesereihenfolge lückenlos durch (0..10).
#      Springende Nummern sehen für einen Kunden wie ein Bug aus — und Georgs
#      ganzer Punkt war "die reports sind zu kompliziert".
#   3. ⭐ JEDER Querverweis "Abschnitt N" zeigt auf den Abschnitt, den er MEINT.
#      Das ist der eigentliche Wert dieser Datei. Ein Verweis, der nach einem
#      Umbau auf den falschen Abschnitt zeigt, ist ein ERFUNDENER Querverweis in
#      einer Auditierbarkeits-Note — und er geht STILL durch: der Auditor prüft
#      Zahlen-Herkunft, nicht Verweis-Korrektheit. Dieser Test fängt jeden
#      künftigen Umbau, nicht nur diesen.
#   4. Das LABEL an der Nachrichtenlage. Georgs "so könnten die beiden dinge
#      zusammenhängen" darf NICHT als Befund auftreten: das Modell ist ein reiner
#      quer-schnittlicher KURS-Ranker, es liest keine Nachrichten. Ohne Label wäre
#      die Deutung des Narrators erfundene Kausalität.
#
# Die Empfehlungs-Blöcke bleiben UNNUMMERIERT (wie "## Empfehlung auf einen Blick"
# es vorher schon war) — sie sind Rahmen, nicht Inhalt.
#
# Deterministisch: kein Netz / LLM / GPU.

import re

from test_report_band_claims import _fs_at
from test_report_render import _fs

from core.report.auditor import audit
from core.report.render import render

# Georgs Reihenfolge, als Abschnitts-Überschriften des echten Renderers.
_GEORG_ORDER = [
    "# NVDA",  # Masthead
    "## 0. Das Unternehmen",  # "das ist die firma / das macht die"
    "## Einschätzung in einem Satz",  # der Call, eine Zeile
    "## 1. Quant-Signal: der Rang",  # "das sagen unsere ml modelle dazu"
    "## 2. Kurs- & Trendbild",
    "## 3. Nachrichtenlage & Marktkontext",  # "news artiekel von jz grad"
    "## 4. Fundamentaldaten & Bewertung",
    "## 5. Pro & Contra",
    "## 6. Investment-These",  # "unsere theorien ... warum sie so steht"
    "## 7. Szenarien",
    "## 8. Risiken",
    "## 9. Insights",
    "## 10. Konfidenz & Grenzen",
    "## Empfehlung",  # "ob man sie kaufen sollte oder nicht"
]

# "## 3. Nachrichtenlage & Marktkontext" -> ("3", "Nachrichtenlage & Marktkontext")
_HEADING_RE = re.compile(r"^## (\d+)\. (.+?)\s*$", re.M)
# "Abschnitt 5 (Pro & Contra)" -> ("5", "Pro & Contra")
# "(Abschnitt 2), die"        -> ("2", "")      -- Klammer gehört zum Verweis, kein Name
# "in Abschnitt 5 belegten"   -> ("5", "")
_XREF_RE = re.compile(r"Abschnitt (\d+)(?:\s+\(([^)]+)\))?")


def _headings(md):
    """{nummer: titel} der tatsächlich gerenderten nummerierten Überschriften."""
    return {int(n): t for n, t in _HEADING_RE.findall(md)}


def test_section_order_matches_georgs_dictated_order():
    md = render(_fs())
    positions = []
    for heading in _GEORG_ORDER:
        idx = md.find(heading)
        assert idx != -1, f"Abschnitt fehlt komplett: {heading!r}"
        positions.append((idx, heading))
    assert positions == sorted(positions), (
        "Reihenfolge weicht ab. Ist:\n  "
        + "\n  ".join(h for _, h in sorted(positions))
        + "\nSoll:\n  "
        + "\n  ".join(_GEORG_ORDER)
    )


def test_numbering_runs_through_without_gaps_in_reading_order():
    """Springende Nummern (0, 2, 3, 5, 6, 4, 1, ...) sehen für einen Kunden wie ein
    Bug aus. Georgs Punkt war Einfachheit — also läuft die Nummer in LESEreihenfolge
    lückenlos durch."""
    md = render(_fs())
    numbers = [int(n) for n, _ in _HEADING_RE.findall(md)]
    assert numbers == list(
        range(len(numbers))
    ), f"Nummerierung springt: {numbers} — erwartet {list(range(len(numbers)))}"
    assert numbers == sorted(numbers), "Nummern müssen in Lesereihenfolge aufsteigen"


def test_every_cross_reference_points_at_the_section_it_means():
    """⭐ DER KERN-TEST. Für jeden Verweis "Abschnitt N" im gerenderten Report:
    Abschnitt N muss existieren — und wo ein Name mitsteht, muss der Name zur
    Überschrift passen. "Abschnitt 5 (Pro & Contra)", das auf einen Abschnitt
    namens "Nachrichtenlage" zeigt, ist schlimmer als gar kein Verweis.

    Ohne narration gerendert: die Querverweise leben in den FALLBACK-Texten der
    Slots (_section_thesis / _section_risks), die nur ohne LLM-Text rendern.
    """
    md = render(_fs())
    headings = _headings(md)
    refs = _XREF_RE.findall(md)
    assert refs, "keine Querverweise gefunden — Regex kaputt oder Text geändert?"

    for num_s, name in refs:
        num = int(num_s)
        assert num in headings, (
            f'Verweis "Abschnitt {num}" zeigt ins Leere — '
            f"es gibt nur {sorted(headings)}"
        )
        if name:
            title = headings[num]
            assert name.lower() in title.lower(), (
                f'Verweis "Abschnitt {num} ({name})" zeigt auf '
                f'"## {num}. {title}" — Nummer und Name passen NICHT zusammen'
            )


def test_one_line_verdict_sits_near_the_top_full_reasoning_at_the_end():
    """Der Leser sieht den Call sofort; die Begründung steht hinten (Georgs Reihenfolge)."""
    md = render(_fs())
    verdict = md.find("## Einschätzung in einem Satz")
    company = md.find("## 0. Das Unternehmen")
    quant = md.find("## 1. Quant-Signal: der Rang")
    full = md.find("## Empfehlung")
    assert -1 < company < verdict < quant, "Ein-Zeilen-Urteil gehört zwischen §0 und §1"
    # die ausführliche Begründung steht NUR hinten
    assert full > md.find("## 10. Konfidenz & Grenzen")
    assert (
        "**Zeithorizont:**" in md[full:]
    ), "Begründung gehört in den Schluss-Abschnitt"
    assert (
        "**Zeithorizont:**" not in md[:full]
    ), "vorne nur EINE Zeile, keine Begründung"


def test_news_section_labels_the_reading_as_a_reading_not_a_model_result():
    """Das Modell liest keine Nachrichten. Ohne dieses Label wäre die Deutung
    darunter erfundene Kausalität in einem Report, dessen ganzer Wert
    Nachvollziehbarkeit ist."""
    md = render(_fs())
    news = md.find("## 3. Nachrichtenlage & Marktkontext")
    nxt = md.find("## 4. Fundamentaldaten")
    section = md[news:nxt]
    assert "Das Modell kennt diese Meldungen nicht" in section
    assert "ausschließlich Kursdaten" in section
    assert "keine Modell-Aussage" in section or "kein Modell-Ergebnis" in section


def test_no_rank_path_still_fails_closed():
    """Ohne Rang: keine Empfehlung, kein Verb, keine Sicherheit — in BEIDEN Blöcken."""
    md = render(_fs(lstm_cross_section={}))
    assert "**Für diesen Stichtag liegt keine Einschätzung vor.**" in md
    assert "**Zeithorizont:**" not in md
    assert "**Kursziel:**" not in md
    # auch die Ein-Zeilen-Fassung darf nichts erfinden
    verdict = md.find("## Einschätzung in einem Satz")
    assert verdict != -1
    head = md[verdict : md.find("## 1. Quant-Signal")]
    assert "Kaufen" not in head and "Verkaufen" not in head and "Halten" not in head


def test_scenarios_section_still_does_not_strike_after_the_reorder():
    """DER TRAP. §7 stellt widersprüchliche Hypothetiken auf (by design). Die
    Carve-out in auditor.py hängt an '## <Zahl>. Szenarien' + Tabellenkopf. Wenn
    die Umnummerierung das bricht, schlägt JEDER Report Alarm."""
    for pct in (10.0, 36.0, 95.0):
        fs = _fs_at(pct)
        band_flags = [f for f in audit(render(fs), fs).flags if f.kind == "band"]
        assert not band_flags, f"§7 schlägt bei pct={pct} an: {band_flags}"
