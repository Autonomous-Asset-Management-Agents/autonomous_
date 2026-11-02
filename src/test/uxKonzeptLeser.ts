import { readFileSync } from "node:fs";
import path from "node:path";

/**
 * STW-10 (#4636) — reads the check steps, states and deviations out of the UX concept, so the
 * operating test is bound to the document and not to a copy of it. Not a test file.
 */

const KONZEPT = "docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md";

export const konzeptText = (): string => readFileSync(path.resolve(process.cwd(), KONZEPT), "utf-8");

function abschnitt(text: string, kopf: string): string {
  const rest = text.slice(text.indexOf(`\n${kopf}\n`) + kopf.length + 2);
  return rest.slice(0, rest.search(/^## /m));
}

/** Body rows of the first table of a section, split into cells. */
function zeilen(text: string, kopf: string): string[][] {
  return abschnitt(text, kopf)
    .split("\n")
    .filter((z) => z.startsWith("|"))
    .slice(2)
    .map((z) => z.split("|").slice(1, -1).map((c) => c.trim()));
}

/** "1, 6" → [1, 6]; "1–7" → [1 … 7]. */
function ablaeufe(zelle: string): number[] {
  const spanne = /^(\d+)–(\d+)$/.exec(zelle);
  if (!spanne) return zelle.split(",").map(Number);
  const [von, bis] = [Number(spanne[1]), Number(spanne[2])];
  return Array.from({ length: bis - von + 1 }, (_, i) => von + i);
}

const kennzeichenDerZeile = (zeile: string[]) => zeile[1].replace(/`/g, "");

export const pruefschritte = (text: string): string[] =>
  [...text.matchAll(/^- \*\*(A\d+-P\d+)\*\*/gm)].map((m) => m[1]);

/** One mark `state:<state>/<flow>` per state and flow of the table in "Zustände". */
export const zustaende = (text: string): string[] =>
  zeilen(text, "## Zustände").flatMap((z) => ablaeufe(z[2]).map((n) => `state:${kennzeichenDerZeile(z)}/${n}`));

/** Text and action of a state; a row with one text per flow separates them with " / ". */
export function zustandsZeile(text: string, kennzeichen: string, ablauf: number): { text: string; aktion: string | null } {
  const zeile = zeilen(text, "## Zustände").find((z) => kennzeichenDerZeile(z) === kennzeichen);
  if (!zeile) throw new Error(`state ${kennzeichen} is not in the concept`);
  const stelle = ablaeufe(zeile[2]).indexOf(ablauf);
  const wahl = (zelle: string) => {
    const teile = zelle.split(" / ");
    return teile.length === 2 ? teile[stelle] : zelle;
  };
  const aktion = wahl(zeile[4]);
  return { text: wahl(zeile[3]), aktion: aktion.startsWith("keine") ? null : aktion };
}

export const abweichungen = (text: string): string[] => zeilen(text, "## Abweichungen").map((z) => z.join(" | "));

const nennt = (zeile: string, kennzeichen: string) =>
  new RegExp(`(^|[^\\w/-])${kennzeichen.replace(/\//g, "\\/")}($|[^\\w-])`).test(zeile);

/**
 * Every mark has exactly one of: one test named `<mark>: …`, or an open entry with a deviation line
 * that names it and is not settled. Returns what is wrong, empty when the concept is covered.
 */
export function abdeckungsLuecken(
  kennzeichen: string[],
  testnamen: string[],
  offen: string[],
  abweichungsZeilen: string[],
): string[] {
  const luecken = offen.filter((k) => !kennzeichen.includes(k)).map((k) => `${k}: not in the concept`);
  for (const k of kennzeichen) {
    const tests = testnamen.filter((n) => n.startsWith(`${k}:`)).length;
    if (offen.includes(k)) {
      if (tests > 0) luecken.push(`${k}: test and open entry`);
      else if (!abweichungsZeilen.some((z) => nennt(z, k) && !z.includes("Erledigt durch"))) {
        luecken.push(`${k}: no deviation line`);
      }
    } else if (tests === 0) luecken.push(`${k}: no test and no deviation`);
    else if (tests > 1) luecken.push(`${k}: ${tests} tests`);
  }
  return luecken;
}
