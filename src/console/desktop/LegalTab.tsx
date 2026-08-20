import { IconShield, IconChevronRight } from "@/console/shared/Icons";

/**
 * Settings → Legal tab (design proposal). A bundled, offline-readable index of every legal
 * document, grouped and linking to the in-app `/legal/*` routes. Console design language, matching
 * the Reports/Decisions pages: `eyebrow` group labels + `surface-flat` cards + muted captions.
 */
interface Doc {
  kind: string;
  title: string;
  desc: string;
}

const GROUPS: { label: string; docs: Doc[] }[] = [
  {
    label: "Trading & risk",
    docs: [
      { kind: "risk-disclosure", title: "Risk Disclosure", desc: "Capital-market & regulatory risk — total-loss warning, execution-only." },
      { kind: "terms", title: "Terms of Service", desc: "Beta terms, execution-only scope, liability, governing law." },
      { kind: "inducements", title: "Conflicts of Interest & Inducements", desc: "PFOF exclusion + CPA/affiliate disclosure (WpHG / MiFID II)." },
    ],
  },
  {
    label: "Company",
    docs: [
      { kind: "imprint", title: "Imprint", desc: "Statutory § 5 DDG / § 18 MStV provider information." },
      { kind: "notice", title: "Legal Notice & Attributions", desc: "Trademarks, proprietary-module exclusions, open-source credits." },
    ],
  },
  {
    label: "Privacy",
    docs: [{ kind: "privacy", title: "Privacy Policy", desc: "GDPR / DSGVO — no phone-home; local, decentralized processing." }],
  },
];

export function LegalTab() {
  return (
    <div className="space-y-6">
      <p className="text-white/45 text-[13px] max-w-xl leading-relaxed">
        Every legal document for autonomous_ — bundled with the app and readable offline. Each opens in-app.
      </p>

      {GROUPS.map((g) => (
        <div key={g.label}>
          <div className="eyebrow mb-2.5">{g.label}</div>
          <div className="space-y-2">
            {g.docs.map((d) => (
              <a
                key={d.kind}
                href={`/legal/${d.kind}`}
                className="surface-flat rounded-xl px-4 py-3.5 flex items-center gap-3 hover:border-white/15 transition-colors"
              >
                <div
                  className="w-9 h-9 rounded-lg flex items-center justify-center shrink-0"
                  style={{ background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)" }}
                >
                  <IconShield width={16} height={16} className="text-white/45" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] font-semibold text-white/90">{d.title}</div>
                  <div className="text-[11.5px] text-white/40 leading-relaxed">{d.desc}</div>
                </div>
                <IconChevronRight width={14} height={14} className="text-white/25 shrink-0" />
              </a>
            ))}
          </div>
        </div>
      ))}

      <div className="surface-flat rounded-xl px-4 py-3.5 text-[11px] text-white/40 leading-relaxed">
        <span className="text-white/60">Autonomous Asset Management Agents UG (haftungsbeschränkt)</span> · Amtsgericht
        Mainz HRB 54409 · Wormser Strasse 5a, 67593 Westhofen, Germany · info@aaagents.de
      </div>
    </div>
  );
}
