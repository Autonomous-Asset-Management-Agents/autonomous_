import React from "react";

/**
 * A tiny, dependency-free Markdown renderer for the RPT-GOLD auditable research
 * note (the Markdown produced by `core/report/render.py`). It deliberately
 * supports ONLY the subset that deterministic renderer emits:
 *   - `#`/`##`/`###` headings
 *   - GitHub-style pipe tables (`| a | b |` + `|---|---|`)
 *   - `-`/`*` bullet lists and `1.` ordered lists
 *   - `>` blockquotes and `---` horizontal rules
 *   - inline `**bold**`, `*italic*`, `` `code` ``, `[text](url)` links
 *   - the report's inline provenance annotations `[src: …]` / `[url: …]`
 *     (one level of nested brackets, e.g. `bars[NVDA]`, mirrors the auditor's
 *     `_SRC_ANNOT_RE`) — rendered as muted "audit tags", NOT dropped, so the
 *     "voll audible" trail stays visible on hover.
 * HTML comments (`<!-- SLOT:… -->`) are stripped.
 *
 * No external Markdown library (CSP-safe, zero bundle risk) and NO
 * `dangerouslySetInnerHTML` — every node is a real React element, so a report
 * string can never inject markup.
 */

type Node = React.ReactNode;

// --- inline -----------------------------------------------------------------

const INLINE = {
  bold: /\*\*([^*]+)\*\*/,
  code: /`([^`]+)`/,
  // [src: …] / [url: …] — allow one level of nested brackets (bars[NVDA]).
  annot: /\[(src|url):\s*((?:[^[\]]|\[[^\]]*\])*?)\s*\]/,
  link: /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/,
  italic: /\*([^*]+)\*/,
  // §3 Monospace-Pflichten: prices, %-returns, ISO dates, latency/size units and N/N vote
  // indices render in JetBrains Mono even inside prose. Deliberately conservative — bare
  // integers, years and ordinals are NOT matched (no false positives on "top 5", "Q3 2026").
  num: /[$€£]\d[\d.,]*|[-+]?\d[\d.,]*\s?%|\d{4}-\d{2}-\d{2}|\d[\d.,]*\s?(?:ms|[KMGT]B|bps)\b|\d+\/\d+(?=\s+(?:BUY|SELL|HOLD|KAUFEN|VERKAUFEN|HALTEN))/,
};

/** Split one line of text into React nodes, honouring the inline grammar. */
function renderInline(text: string, keyPrefix: string): Node[] {
  const out: Node[] = [];
  let rest = text;
  let k = 0;
  const emit = (n: Node) => out.push(<React.Fragment key={`${keyPrefix}-${k++}`}>{n}</React.Fragment>);

  while (rest.length > 0) {
    // find the earliest-matching inline token
    let best: { idx: number; len: number; node: Node } | null = null;
    const consider = (m: RegExpExecArray | null, node: Node) => {
      if (m && (best === null || m.index < best.idx)) {
        best = { idx: m.index, len: m[0].length, node };
      }
    };
    let m: RegExpExecArray | null;
    if ((m = INLINE.bold.exec(rest))) consider(m, <strong className="font-semibold text-white/95">{m[1]}</strong>);
    if ((m = INLINE.code.exec(rest)))
      consider(m, <code className="rounded bg-white/10 px-1 py-0.5 text-[12px] text-white/80">{m[1]}</code>);
    if ((m = INLINE.annot.exec(rest)))
      consider(
        m,
        <span
          className="ml-1 rounded bg-white/[0.06] px-1 align-middle text-[9.5px] uppercase tracking-wide text-white/35"
          title={m[2]}
        >
          {m[1]}
        </span>,
      );
    if ((m = INLINE.link.exec(rest)))
      consider(
        m,
        <a
          href={m[2]}
          target="_blank"
          rel="noreferrer noopener"
          className="text-white/90 underline decoration-white/25 underline-offset-2 hover:decoration-white/60"
        >
          {m[1]}
        </a>,
      );
    if ((m = INLINE.italic.exec(rest))) consider(m, <em className="italic text-white/85">{m[1]}</em>);
    if ((m = INLINE.num.exec(rest))) consider(m, <span className="num">{m[0]}</span>);

    if (best === null) {
      emit(rest);
      break;
    }
    const b: { idx: number; len: number; node: Node } = best;
    if (b.idx > 0) emit(rest.slice(0, b.idx));
    emit(b.node);
    rest = rest.slice(b.idx + b.len);
  }
  return out;
}

// --- blocks -----------------------------------------------------------------

const isTableRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isTableSep = (l: string) => /^\s*\|[\s:|-]+\|\s*$/.test(l) && l.includes("-");
const cells = (l: string) =>
  l
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((c) => c.trim());

// §4B / §3: financial figures and ticker symbols render in JetBrains Mono (.num) so columns
// of figures align on fixed widths; prose/label cells stay in the body font. A cell qualifies
// when it is a pure number/currency/percent/unit/ratio/date, or a 1–5 char all-caps code (ticker).
const DATA_CELL =
  /^[$€£]?[-+]?[\d.,]+\s*(?:%|x|bps|[KMB]|ms|[KMGT]B|s)?$|^\d+\s*\/\s*\d+$|^[A-Z]{1,5}$|^\d{4}-\d{2}-\d{2}$/;
const isDataCell = (t: string) => DATA_CELL.test(t.trim());

/** Parse the full note into a flat list of block-level React elements. */
function parseBlocks(source: string): Node[] {
  const src = (source ?? "").replace(/<!--[\s\S]*?-->/g, "");
  const lines = src.split(/\r?\n/);
  const blocks: Node[] = [];
  let i = 0;
  let key = 0;
  const nextKey = () => `b${key++}`;

  while (i < lines.length) {
    const line = lines[i];

    // blank
    if (line.trim() === "") {
      i++;
      continue;
    }

    // horizontal rule
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      blocks.push(<hr key={nextKey()} className="border-white/10" />);
      i++;
      continue;
    }

    // heading
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = h[1].length;
      const cls =
        level <= 1
          ? "text-[18px] font-bold text-white/95 mt-1"
          : level === 2
            ? "text-[15px] font-semibold text-white/90 mt-3 border-t border-white/5 pt-3"
            : "text-[13px] font-semibold text-white/80 mt-2";
      const content = renderInline(h[2], nextKey());
      blocks.push(
        React.createElement(`h${Math.min(level, 4)}`, { key: nextKey(), className: cls }, content),
      );
      i++;
      continue;
    }

    // table
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const header = cells(line);
      i += 2; // skip header + separator
      const rows: string[][] = [];
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(cells(lines[i]));
        i++;
      }
      blocks.push(
        <div key={nextKey()} className="overflow-x-auto">
          <table className="w-full border-collapse text-[12.5px]">
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th
                    key={ci}
                    className="border-b border-white/5 px-2 py-1 text-left text-[11px] font-semibold uppercase tracking-[0.04em] text-white/[0.16]"
                  >
                    {renderInline(c, `th${ci}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} className="border-b border-white/5">
                  {r.map((c, ci) => (
                    <td
                      key={ci}
                      className={`px-2 py-1 align-top text-white/80${isDataCell(c) ? " num" : ""}`}
                    >
                      {renderInline(c, `td${ri}-${ci}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // blockquote
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quote.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote
          key={nextKey()}
          className="border-l-2 border-white/15 pl-3 text-[12.5px] italic text-white/60"
        >
          {renderInline(quote.join(" "), nextKey())}
        </blockquote>,
      );
      continue;
    }

    // unordered / ordered list
    const listKind = /^\s*[-*]\s+/.test(line) ? "ul" : /^\s*\d+\.\s+/.test(line) ? "ol" : null;
    if (listKind) {
      const items: string[] = [];
      const re = listKind === "ul" ? /^\s*[-*]\s+/ : /^\s*\d+\.\s+/;
      while (i < lines.length && re.test(lines[i])) {
        items.push(lines[i].replace(re, ""));
        i++;
      }
      const inner = items.map((it, ii) => (
        <li key={ii} className="leading-relaxed">
          {renderInline(it, `li${ii}`)}
        </li>
      ));
      blocks.push(
        listKind === "ul" ? (
          <ul key={nextKey()} className="list-disc space-y-1 pl-5 text-white/85">
            {inner}
          </ul>
        ) : (
          <ol key={nextKey()} className="list-decimal space-y-1 pl-5 text-white/85">
            {inner}
          </ol>
        ),
      );
      continue;
    }

    // paragraph — accumulate consecutive plain lines
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== "" &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*([-*_])\1{2,}\s*$/.test(lines[i]) &&
      !isTableRow(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*[-*]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={nextKey()} className="leading-relaxed text-white/85">
        {renderInline(para.join(" "), nextKey())}
      </p>,
    );
  }

  return blocks;
}

export function MarkdownNote({ source }: { source: string }) {
  if (!source || !source.trim()) return null;
  return <div className="report-note space-y-2.5 text-[13px]">{parseBlocks(source)}</div>;
}

export default MarkdownNote;
