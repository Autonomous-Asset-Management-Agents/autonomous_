/*
 * Copyright 2026 Andreas Apeldorn, Georg Apeldorn / Autonomous Asset Management Agents UG
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * GTM-1 T1 (#1464) — the public /support page.
 *
 * Renders the SINGLE canonical FAQ source `docs/oss/FAQ.md` (imported raw, no duplication —
 * edit the FAQ there, not here) with the same landing-b chrome as the Legal pages, plus a
 * concrete "Get help" block (GitHub Discussions / Issues / email). Self-service-first; the FAQ
 * itself carries the "technical support, not investment advice" boundary.
 */
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import faqMarkdown from "../../docs/oss/FAQ.md?raw";
import "@/styles/landing-b.css";

// GitHub org (kept in sync with the landing footer's GitHub links).
const GH = "https://github.com/Autonomous-Asset-Management-Agents/Dev-Enviroment";
const SUPPORT_EMAIL = "security@aaagents.de";

/** Inline markdown: **bold**, `code`, [text](url). */
function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`(.+?)`|\[(.+?)\]\((.+?)\)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1] !== undefined) out.push(<strong key={k++}>{m[1]}</strong>);
    else if (m[2] !== undefined) out.push(<code key={k++}>{m[2]}</code>);
    else if (m[3] !== undefined)
      out.push(
        <a key={k++} href={m[4]} target="_blank" rel="noreferrer">
          {m[3]}
        </a>,
      );
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** Tiny markdown subset (headings, blockquote, paragraphs) — enough for the FAQ. */
function renderMarkdown(md: string): ReactNode[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.trim() === "" || line.trim().startsWith("<!--")) {
      i++;
      continue;
    }
    if (line.startsWith("### ")) {
      blocks.push(<h3 key={key++}>{renderInline(line.slice(4))}</h3>);
      i++;
    } else if (line.startsWith("## ")) {
      blocks.push(<h2 key={key++}>{renderInline(line.slice(3))}</h2>);
      i++;
    } else if (line.startsWith("# ")) {
      blocks.push(<h1 key={key++}>{renderInline(line.slice(2))}</h1>);
      i++;
    } else if (line.startsWith("> ")) {
      const quote: string[] = [];
      while (i < lines.length && lines[i].startsWith("> ")) {
        quote.push(lines[i].slice(2));
        i++;
      }
      blocks.push(<blockquote key={key++}>{renderInline(quote.join(" "))}</blockquote>);
    } else {
      const para: string[] = [];
      while (i < lines.length && lines[i].trim() !== "" && !/^(#{1,3} |> )/.test(lines[i])) {
        para.push(lines[i]);
        i++;
      }
      blocks.push(<p key={key++}>{renderInline(para.join(" "))}</p>);
    }
  }
  return blocks;
}

export default function Support() {
  const navigate = useNavigate();
  return (
    <div className="landing-b-root">
      <nav className="lb-nav">
        <button
          className="lb-nav-logo"
          onClick={() => navigate("/")}
          style={{ background: "none", border: "none", cursor: "pointer" }}
        >
          aaagents<span style={{ color: "#00c27a" }}>_</span>
        </button>
        <div className="lb-nav-right">
          <button className="lb-nav-link" onClick={() => navigate("/")}>
            ← Back to home
          </button>
        </div>
      </nav>
      <article
        className="lb-container"
        style={{ padding: "60px var(--lb-gutter) 120px", maxWidth: 820 }}
      >
        <div
          className="lb-eyebrow"
          style={{
            fontFamily: "var(--lb-mono)",
            fontSize: 12,
            letterSpacing: 2.5,
            textTransform: "uppercase",
            color: "var(--lb-muted-light)",
            marginBottom: 16,
          }}
        >
          Help &amp; Support
        </div>

        <div className="support-faq" style={{ color: "var(--lb-fg-light)", lineHeight: 1.7 }}>
          {renderMarkdown(faqMarkdown)}
        </div>

        <h2>Get help</h2>
        <p>
          Search the FAQ above first. Then:{" "}
          <a href={`${GH}/discussions`} target="_blank" rel="noreferrer">
            GitHub Discussions
          </a>{" "}
          for questions,{" "}
          <a href={`${GH}/issues`} target="_blank" rel="noreferrer">
            GitHub Issues
          </a>{" "}
          for bugs, or <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a> for security/private
          matters. The free edition is best-effort community support (no SLA).
        </p>
        <p style={{ color: "var(--lb-muted-light)", fontSize: 13 }}>
          <strong>Technical support only — not investment advice.</strong> Never share your API
          keys; logs may contain secrets, so redact before posting.
        </p>
      </article>
    </div>
  );
}
