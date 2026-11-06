import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { listStrategies } from "@/lib/api";
import { getFleetSession, getSetupState, isDesktop, relaunchApp, saveSetupState } from "@/lib/desktopBridge";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";
import { WorkshopState } from "@/console/desktop/strategyWorkshop/WorkshopStates";
import { loadFailure } from "@/console/desktop/strategyWorkshop/errorText";

/**
 * "Enable strategies" on top of Settings → Trading → Strategies (#5166, owner decision of
 * 05.11.2026; replaces the switch on "Paper accounts", STW-2f #5125).
 *
 * Two separate questions, as on the old switch:
 * - choice: ONLY a boolean `strategyProfilesEnabled` in setup.json (`engine-fleet.cjs::profilesFlagOn`);
 * - active: does the engine of THIS session carry the strategy routes. The shell answers it with
 *   `getFleetSession().fleetActive` (the fleet is built once at app start); without a session
 *   (browser, older shell) one `GET /api/strategies` decides — 404 is off, anything else on, so a
 *   stopped engine still shows the cards with their own `engine-offline` notice.
 *
 * The box shows `choice ?? active`. Enable and Disable never write directly: a dialog names the
 * consequences, then "… restart later" writes the boolean and "… & restart app" writes it and
 * restarts the app through `relaunchApp` (#5142). Choice ≠ session asks for the restart.
 *
 * `children` (the strategy cards) mount only while active. Otherwise their titles stand greyed and
 * inert without content, and nothing asks the engine (plan #5166 §2.3, §6 Punkt 3).
 *
 * Design (guide §7, §9, §15): the box is the page's explanation box like on the other tabs; Enable
 * is `.btn btn-primary` (an action with a confirmation, not a boolean toggle, so no `OnOffSegment`);
 * the state is a `StatusDot`; the dialog is neutral — white text, `.btn` only.
 */

type Gate = { load: "loading" | "ready"; choice: boolean | null; active: boolean; sessionActive: boolean | null };
type Dialog = "enable" | "disable" | "info" | "restart" | null;

const GREYED_TITLES = [
  "console.strategyWorkshop.T38",
  "console.strategyWorkshop.T10",
  "console.strategyWorkshop.T01",
  "console.strategyWorkshop.T16",
  "console.strategyWorkshop.T34",
];
const POINTS = ["named", "accounts", "memory", "restart", "off"];

async function readChoice(): Promise<boolean | null> {
  try {
    const s = await getSetupState();
    return typeof s.strategyProfilesEnabled === "boolean" ? s.strategyProfilesEnabled : null;
  } catch (err) {
    console.warn("strategiesGate: getSetupState failed → no operator choice, the session decides", err);
    return null;
  }
}

async function readActive(session: { fleetActive: boolean } | null): Promise<boolean> {
  if (session) return session.fleetActive;
  try {
    await listStrategies();
    return true;
  } catch (e) {
    return loadFailure(e) !== "hidden";
  }
}

export function StrategiesGate({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [gate, setGate] = useState<Gate>({ load: "loading", choice: null, active: false, sessionActive: null });
  const [attempt, setAttempt] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [writeFailed, setWriteFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    void (async () => {
      const choice = await readChoice();
      const session = await getFleetSession();
      const active = await readActive(session);
      if (alive) setGate({ load: "ready", choice, active, sessionActive: session?.fleetActive ?? null });
    })();
    return () => {
      alive = false;
    };
  }, [attempt]);

  const enabled = gate.choice ?? gate.active;
  const restartRequired =
    gate.load === "ready" && gate.sessionActive !== null && gate.choice !== null && gate.choice !== gate.sessionActive;
  const desktop = isDesktop();
  const s = (key: string) => t(`console.strategyWorkshop.strategies.${key}`);

  // Writes the boolean once; "now" restarts the app afterwards. A failed write restarts nothing.
  const apply = async (next: boolean, now: boolean) => {
    setDialog(null);
    setWriteFailed(false);
    setBusy(true);
    try {
      await saveSetupState({ strategyProfilesEnabled: next });
    } catch (err) {
      console.warn("strategiesGate: saveSetupState failed → choice unchanged", err);
      setWriteFailed(true);
      setBusy(false);
      return;
    }
    if (now) {
      await relaunchApp();
    }
    setBusy(false);
    setAttempt((n) => n + 1);
  };

  const confirming = dialog === "enable" || dialog === "disable";
  const next = dialog === "enable";

  return (
    <>
      <section aria-label={s("titleInfo")} className="surface-flat rounded-xl px-5 py-4 flex items-start gap-3">
        <IconLightbulb width={16} height={16} className="text-white/70 mt-1 shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-4 flex-wrap">
            <div className="max-w-xl text-[13.5px] text-white/70 leading-relaxed">
              <p>
                <span className="font-semibold text-white/90">{s("lead")}</span> {s("sentence")}
              </p>
              <button type="button" className="btn-link mt-2" onClick={() => setDialog("info")}>
                {s("howItWorks")}
              </button>
            </div>
            {gate.load === "ready" && desktop && (
              enabled ? (
                <div className="flex items-center gap-3">
                  <StatusDot tone="on">{s("enabled")}</StatusDot>
                  <button type="button" className="btn-link" disabled={busy} onClick={() => setDialog("disable")}>
                    {s("disable")}
                  </button>
                </div>
              ) : (
                <button type="button" className="btn btn-primary" disabled={busy} onClick={() => setDialog("enable")}>
                  {s("enable")}
                </button>
              )
            )}
            {gate.load === "ready" && !desktop && enabled && <StatusDot tone="on">{s("enabled")}</StatusDot>}
          </div>

          {writeFailed && (
            <div className="mt-3">
              <WorkshopState kind="error" />
            </div>
          )}
          {restartRequired && (
            <div className="mt-3">
              <WorkshopState
                kind="empty"
                text={s("restartRequired")}
                action={t("console.strategyWorkshop.deviation.restartApp")}
                onAction={() => setDialog("restart")}
              />
            </div>
          )}
        </div>
      </section>

      <AlertDialog open={dialog !== null} onOpenChange={(open) => !open && setDialog(null)}>
        <AlertDialogContent className="max-w-[520px] rounded-[20px] border-white/10 bg-[#1c1c1e] text-white">
          {/* The portal lands on document.body, outside `.aaa-console` — the scope wrapper (as in
              LiveConsentModal, guide §17) lets `.btn` apply; `contents` keeps the dialog's layout. */}
          <div className="aaa-console contents">
            {dialog === "restart" ? (
              <AlertDialogHeader>
                <AlertDialogTitle>{t("console.strategyWorkshop.deviation.restartConfirm")}</AlertDialogTitle>
              </AlertDialogHeader>
            ) : (
              <AlertDialogHeader>
                <AlertDialogTitle>
                  {dialog === "enable" ? s("titleEnable") : dialog === "disable" ? s("titleDisable") : s("titleInfo")}
                </AlertDialogTitle>
                <ul className="mt-2 rounded-lg border border-white/10 bg-white/[0.03] px-4 py-3 flex flex-col gap-2 text-[13px] text-white/70 leading-relaxed list-disc pl-8">
                  {POINTS.map((p) => (
                    <li key={p}>
                      <span className="font-semibold text-white/90">{s(`points.${p}.lead`)}</span> {s(`points.${p}.text`)}
                    </li>
                  ))}
                </ul>
              </AlertDialogHeader>
            )}
            <AlertDialogFooter>
              {dialog === "info" ? (
                <AlertDialogCancel className="btn">{t("console.strategyWorkshop.deviation.close")}</AlertDialogCancel>
              ) : dialog === "restart" ? (
                <>
                  <AlertDialogCancel className="btn">{t("console.strategyWorkshop.extra.cancel")}</AlertDialogCancel>
                  <AlertDialogAction className="btn" onClick={() => void relaunchApp()}>
                    {t("console.strategyWorkshop.deviation.restart")}
                  </AlertDialogAction>
                </>
              ) : (
                confirming && (
                  <>
                    <AlertDialogCancel className="btn">{t("console.strategyWorkshop.extra.cancel")}</AlertDialogCancel>
                    <AlertDialogAction className="btn" onClick={() => void apply(next, false)}>
                      {s(next ? "enableLater" : "disableLater")}
                    </AlertDialogAction>
                    <AlertDialogAction className="btn btn-primary" onClick={() => void apply(next, true)}>
                      {s(next ? "enableNow" : "disableNow")}
                    </AlertDialogAction>
                  </>
                )
              )}
            </AlertDialogFooter>
          </div>
        </AlertDialogContent>
      </AlertDialog>

      {gate.load === "ready" &&
        (gate.active ? (
          children
        ) : (
          <div className="space-y-3">
            <p className="text-[13px] text-white/55">{s("greyed")}</p>
            {/* React 18 has no typed `inert`; the spread sets the plain attribute. */}
            <div data-testid="strategies-greyed" {...{ inert: "" }} aria-disabled="true" className="opacity-40 space-y-6">
              {GREYED_TITLES.map((key) => (
                <div key={key} className="surface p-6">
                  <div className="eyebrow">{t(key)}</div>
                </div>
              ))}
            </div>
          </div>
        ))}
    </>
  );
}
