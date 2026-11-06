/** i18n coverage guard for strategy workshop (STW-4e #4892, Epic #4625). */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ts from "typescript";
import { GERMAN_ENABLED } from "@/lib/languageGate";

const NS = "console.strategyWorkshop";
// #4918: flows 3 and 5 (Simulate). Flow 4 is only its notice T21 there, so it stays out.
// #4934: flow 2 (Paper accounts, console cut).
const BUILT_FLOWS = [1, 2, 3, 5, 6, 7];
const ENGINE_ERROR_CODES = [
  "strategy_name_length", "strategy_name_reserved", "guardrail_in_strategy", "strategy_limit_reached",
  "unknown_setting", "setting_value_not_allowed", "setting_value_type",
  "setting_value_out_of_range", "setting_text_too_long", "ranking_prompt_missing",
  "ranking_list_missing", "request_invalid", "strategy_not_found",
  "nonce_reused", "hitl_required", "strategy_assigned", "sizing_change_unconfirmed",
  "audit_write_failed", "strategy_store_unreadable", "audit_chain_unreadable",
];

const SOURCES = import.meta.glob<string>("../console/desktop/strategyWorkshop/**/*.{ts,tsx}", {
  eager: true, query: "?raw", import: "default",
});
const ERROR_TEXT = import.meta.glob<{ KNOWN_ERROR_CODES: readonly string[] }>(
  "../console/desktop/strategyWorkshop/errorText.ts", { eager: true },
);
const EXCLUDED = ["acknowledgments.ts"];
const sources = () => Object.entries(SOURCES).filter(([file]) => !EXCLUDED.some((x) => file.endsWith(`/${x}`)));

const en = JSON.parse(readFileSync(resolve(process.cwd(), "src/locales/en/translation.json"), "utf8"));
const de = JSON.parse(readFileSync(resolve(process.cwd(), "src/locales/de/translation.json"), "utf8"));
const konzept = readFileSync(resolve(process.cwd(), "docs/4628-ux-konzept-strategie-werkstatt/ux_konzept.md"), "utf8");

const get = (o: unknown, key: string): unknown =>
  key.split(".").reduce<unknown>((x, p) => (x && typeof x === "object" ? (x as Record<string, unknown>)[p] : undefined), o);

function flatValues(obj: unknown, prefix = ""): [string, string][] {
  if (typeof obj === "string") return [[prefix.slice(0, -1), obj]];
  if (!obj || typeof obj !== "object") return [];
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => flatValues(v, `${prefix}${k}.`));
}

const TEXT_ATTRIBUTES = new Set(["aria-label", "title", "placeholder", "alt", "label", "emptyHint", "text", "action"]);
const TEXT_PROPERTIES = new Set(["label", "desc", "title", "text"]);
const hasLetter = (s: string) => /\p{L}/u.test(s);
const looksLikeProse = (s: string) => /^\p{Lu}(\p{Ll}|\p{Lu}*\s)/u.test(s);

function isTranslateArgument(node: ts.Node): boolean {
  const call = node.parent;
  return ts.isCallExpression(call) && ts.isIdentifier(call.expression) && call.expression.text === "t";
}

function rendersInJsx(node: ts.Node): boolean {
  let child: ts.Node = node;
  for (let p = node.parent; p; child = p, p = p.parent) {
    if (ts.isJsxExpression(p)) return ts.isJsxAttribute(p.parent) ? TEXT_ATTRIBUTES.has(p.parent.name.getText()) : true;
    if (ts.isParenthesizedExpression(p)) continue;
    if (ts.isConditionalExpression(p) && p.condition !== child) continue;
    if (ts.isBinaryExpression(p) && p.right === child &&
      [ts.SyntaxKind.AmpersandAmpersandToken, ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(p.operatorToken.kind)) continue;
    return false;
  }
  return false;
}

function literalsIn(file: string, source: string): string[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const found: string[] = [];
  const report = (node: ts.Node, text: string) => {
    const { line } = sf.getLineAndCharacterOfPosition(node.getStart());
    found.push(`${file.split("/").pop()}:${line + 1}: ${JSON.stringify(text)}`);
  };
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) {
      if (hasLetter(node.text)) report(node, node.text.trim());
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node)) {
      const text = node.text, parent = node.parent;
      const skip = ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent) ||
        ts.isLiteralTypeNode(parent) || isTranslateArgument(node) || text.startsWith(`${NS}.`);
      if (!skip && hasLetter(text)) {
        const attr = ts.isJsxAttribute(parent) ? parent.name.getText(sf) : null;
        const prop = ts.isPropertyAssignment(parent) && parent.initializer === node ? parent.name.getText(sf) : null;
        if ((attr !== null && TEXT_ATTRIBUTES.has(attr)) || (prop !== null && TEXT_PROPERTIES.has(prop)) ||
          (attr === null && rendersInJsx(node)) || looksLikeProse(text)) {
          report(node, text);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return found;
}

function section(heading: string): string {
  const parts = konzept.split(/^(## .+)$/m);
  const i = parts.findIndex((p) => p.trim() === heading);
  expect(i, `section ${heading} missing in ux_konzept.md`).toBeGreaterThan(-1);
  return parts[i + 1];
}

function table(text: string): Record<string, string>[] {
  const rows = text.split("\n").filter((l) => l.trim().startsWith("|"))
    .map((l) => l.trim().slice(1, -1).split(/(?<!\\)\|/).map((c) => c.trim()));
  const [head, , ...body] = rows;
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h, r[i] ?? ""])));
}

function flowsOf(cell: string): number[] {
  return cell.split(",").flatMap((p) => {
    const [a, b] = p.trim().split(/[–-]/).map(Number);
    return b ? Array.from({ length: b - a + 1 }, (_, i) => a + i) : [a];
  });
}
const built = (cell: string) => flowsOf(cell).some((f) => BUILT_FLOWS.includes(f));

function conceptTexts(): [string, string][] {
  const out: [string, string][] = [];
  for (const row of table(section("## Texte"))) {
    if (built(row["Ablauf"])) out.push([`${NS}.${row["ID"]}`, row["UI-Text"].replace(/^`|`$/g, "")]);
  }
  for (const row of table(section("## Zustände"))) {
    if (!built(row["Abläufe"])) continue;
    const id = row["Kennzeichen"].replace(/`/g, "");
    const texts = row["UI-Text"].split(" / ");
    const actions = /^keine\b/.test(row["Aktion"]) ? [] : row["Aktion"].split(" / ");
    const flows = flowsOf(row["Abläufe"]);
    if (texts.length > 1) {
      texts.forEach((t, i) => built(String(flows[i])) && out.push([`${NS}.state.${id}.text.${flows[i]}`, t]));
      actions.forEach((a, i) => built(String(flows[i])) && out.push([`${NS}.state.${id}.action.${flows[i]}`, a]));
    } else {
      out.push([`${NS}.state.${id}.text`, texts[0]]);
      if (actions[0]) out.push([`${NS}.state.${id}.action`, actions[0]]);
    }
  }
  return out;
}

const BANNED = [
  /\bbest\b/i, /recommend/i, /empfohlen/i, /winner/i, /\btop\b/i, /top-ranked/i, /\brank/i,
  /outperform/i, /\bsafe(r|st)?\b/i, /risky/i, /guarantee/i, /aggressive/i, /conservative/i, /profile/i, /sharpe/i,
];

describe("strategy workshop i18n coverage (STW-4e #4892)", () => {
  it("C1: no user-visible literal in strategyWorkshop/**/*.{ts,tsx}", () => {
    expect(sources().length, "workshop sources found").toBeGreaterThan(4);
    expect(sources().flatMap(([file, src]) => literalsIn(file, src))).toEqual([]);
  });

  it("C2: every concept text of the built flows is in the en catalogue, byte for byte", () => {
    const expected = conceptTexts();
    expect(expected.length, "concept texts of the built flows").toBeGreaterThan(30);
    const wrong = expected.filter(([key, value]) => get(en, key) !== value).map(([key, value]) => `${key} ≠ ${value}`);
    expect(wrong).toEqual([]);
  });

  it("C3: every catalogue key the workshop asks for exists in en", () => {
    const asked = sources().flatMap(([, src]) =>
      [...src.matchAll(/["'`](console\.strategyWorkshop\.[A-Za-z0-9_.]+)["'`]/g)].map((m) => m[1]),
    );
    expect(asked.length, "the workshop reads the catalogue").toBeGreaterThan(40);
    expect(asked.filter((k) => typeof get(en, k) !== "string"), "missing in en").toEqual([]);
  });

  it("C4: English only — de mirrors en byte for byte and German stays off", () => {
    expect(get(en, NS), "namespace in en").toBeTypeOf("object");
    expect(get(de, NS)).toEqual(get(en, NS));
    expect(GERMAN_ENABLED).toBe(false);
  });

  it("C5: no catalogue value matches the neutral word list", () => {
    const hits = flatValues(get(en, NS)).flatMap(([k, v]) =>
      BANNED.filter((re) => re.test(v)).map((re) => `${k}: ${re}`),
    );
    expect(hits).toEqual([]);
  });

  it("C7 #4940: both prompt hints are in the catalogue and in the concept's deviations", () => {
    const hints = {
      localModelHint: "Local model: no web search. Answers are based on the model's training data and may be out of date.",
      cloudCostHint: "Web search requests may be billed by this provider above its free quota.",
    };
    const abweichungen = section("## Abweichungen");
    for (const [key, text] of Object.entries(hints)) {
      expect(get(en, `${NS}.prompt.${key}`)).toBe(text);
      expect(abweichungen).toContain(text);
    }
  });

  it("C6: every engine error code has a catalogue text", () => {
    const mod = Object.values(ERROR_TEXT)[0];
    expect(mod, "errorText.ts exists").toBeDefined();
    const KNOWN_ERROR_CODES = mod.KNOWN_ERROR_CODES;
    expect([...KNOWN_ERROR_CODES].sort()).toEqual([...ENGINE_ERROR_CODES].sort());
    expect(KNOWN_ERROR_CODES.filter((c) => typeof get(en, `${NS}.errors.${c}`) !== "string")).toEqual([]);
  });
});
