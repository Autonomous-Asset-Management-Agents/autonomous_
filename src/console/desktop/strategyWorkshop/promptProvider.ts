import type { LlmTrichterResponse } from "@/lib/api";

/**
 * Effective provider of the prompt run (#4940). The console never derives the system provider
 * itself: it reads `anbieter` of the last run from the engine (#4960) and only decides when that
 * value says nothing about `system`.
 */

const SYSTEM = "system";
const LOCAL = "ollama";
const CLOUD = ["gemini", "openai", "anthropic"];

export interface EffektiverAnbieter {
  istLokal: boolean;
  /** Only an explicit choice: `system` never searches the web (#4960), so it costs no search. */
  istCloud: boolean;
}

export function effektiverAnbieter(anbieter: string, systemAnbieter: string | null): EffektiverAnbieter {
  return {
    istLokal: anbieter === LOCAL || (anbieter === SYSTEM && systemAnbieter === LOCAL),
    istCloud: CLOUD.includes(anbieter),
  };
}

/**
 * `anbieter` of the funnel is the provider of the last run, which is the system provider only if
 * that run went through `system`. A run with web search was a chosen provider, and so is any run
 * while the engine is set to one. Unknown stays `null`: the editor then shows plain "System".
 */
export function systemAnbieterAus(
  lauf: Pick<LlmTrichterResponse, "anbieter" | "websuche"> | null,
  engineWahl: string | undefined,
): string | null {
  if (engineWahl !== SYSTEM || lauf?.websuche === true) return null;
  return lauf?.anbieter ?? null;
}
