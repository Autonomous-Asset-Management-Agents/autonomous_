import { useEffect, useState } from "react";
import { fetchLlmTrichter, type LlmTrichterResponse } from "@/lib/api";
import { StatusDot } from "@/console/shared/StatusDot";
import {
  aktiveQuelle,
  alterText,
  endstatusText,
  grundText,
  hinweisText,
  listeStatusText,
  statusTone,
  stufenText,
} from "@/console/shared/llmTrichterGruende";

const POLL_MS = 60_000;

/**
 * #4658 (Epic #4638): the LLM-QA funnel on the Decisions page — how many LLM proposals reached
 * the universe, the Round Table and the Iron Dome, each with status and readable reason, plus the
 * age/status of the current list and the active ranking source.
 *
 * Hidden while the ranking source is the LSTM panel: GET /llm-vorauswahl/trichter answers an empty
 * funnel (no run, `liste_status = keiner`) until the LLM pre-selection has delivered a run. Counts
 * come from the engine verbatim; nothing here is a fixed number.
 */
function istSichtbar(t: LlmTrichterResponse | null): t is LlmTrichterResponse {
  return t != null && !(t.lauf_id == null && t.liste_status === "keiner");
}

function useLlmTrichter(): LlmTrichterResponse | null {
  const [data, setData] = useState<LlmTrichterResponse | null>(null);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const loop = async () => {
      if (!alive) return;
      try {
        const resp = await fetchLlmTrichter();
        // A failed poll keeps the last value (same rule as useRoundTablePolling).
        if (alive && resp) setData(resp);
      } catch {
        // Graceful fallback: Polling behält letzten Zustand bei
      } finally {
        if (alive) timer = setTimeout(loop, POLL_MS);
      }
    };
    void loop();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, []);
  return data;
}

const thBase = "px-4 py-2.5 text-[10px] font-semibold uppercase tracking-wider";

export function LlmTrichter() {
  const t = useLlmTrichter();
  if (!istSichtbar(t)) return null;

  const texte = stufenText(t.quelle);
  const stufen = [
    { n: t.n_vorgeschlagen, label: texte.vorgeschlagen },
    { n: t.n_universum, label: texte.universum },
    { n: t.n_round_table, label: texte.round_table },
    { n: t.n_iron_dome, label: texte.iron_dome },
  ];
  const alter = alterText(t.liste_alter_s);

  return (
    <section className="space-y-3" aria-label="LLM-Vorauswahl">
      <div className="flex items-baseline gap-4 flex-wrap">
        <div className="eyebrow">LLM-Vorauswahl</div>
        <span data-testid="trichter-liste" className="inline-flex items-center gap-3">
          <StatusDot tone={statusTone(t.liste_status)}>
            Liste: {listeStatusText(t.liste_status)}
          </StatusDot>
          {alter && <span className="num text-white/45 text-[11px]">Alter {alter}</span>}
          <span className="pill !text-[9px] !px-1.5 !py-0.5">
            Quelle: {aktiveQuelle(t.liste_status)}
          </span>
        </span>
        {t.websuche != null && (
          <span data-testid="trichter-websuche" className="pill !text-[9px] !px-1.5 !py-0.5">
            Web search: {t.websuche ? "yes" : "no"}
          </span>
        )}
      </div>

      {t.hinweis && (
        <p data-testid="trichter-hinweis" className="text-[11px] text-white/45">
          {hinweisText(t.hinweis)}
        </p>
      )}

      <div className="surface-flat rounded-xl px-5 py-4 flex items-stretch gap-2 flex-wrap">
        {stufen.map((s, i) => (
          <div key={s.label} className="flex items-center gap-2">
            {i > 0 && <span className="text-white/30 text-[13px]">→</span>}
            <div data-testid="trichter-stufe" className="px-2">
              <div className="num text-[20px] font-bold text-white/92">{s.n}</div>
              <div className="text-[11px] text-white/45">{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {t.symbole.length > 0 && (
        <div className="surface overflow-hidden">
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-white/16">
                <th className={`${thBase} text-left`}>Symbol</th>
                <th className={`${thBase} text-left`}>Status</th>
                <th className={`${thBase} text-left`}>Grund</th>
              </tr>
            </thead>
            <tbody>
              {t.symbole.map((s) => (
                <tr
                  key={`${s.status}-${s.symbol}`}
                  data-testid={`trichter-zeile-${s.symbol}`}
                  className="border-t border-white/5"
                >
                  <td className="px-4 py-2.5 font-bold tracking-tight2 text-white/92">{s.symbol}</td>
                  <td className="px-4 py-2.5 text-white/70">{endstatusText(s.status)}</td>
                  <td className="px-4 py-2.5 text-white/45">{grundText(s.grund)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
