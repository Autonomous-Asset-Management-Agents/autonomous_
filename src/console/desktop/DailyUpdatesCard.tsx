import { useState, useEffect } from "react";
import { isDesktop, saveSecret, saveSetupState, getSetupState, claimBeta } from "@/lib/desktopBridge";
import { fetchEntitlementStatus } from "@/lib/api";
import { useStore } from "@/console/store/useStore";
import { currencySymbol } from "@/console/lib/format";
import { IconLightbulb } from "@/console/shared/Icons";
import { StatusDot } from "@/console/shared/StatusDot";

const INPUT_CLASS =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

// Always attached to every report (BaFin execution-only: a private report of your
// own data is fine; it must never read as advice).
const LEGAL_LINE =
  "Not investment advice. Past performance is not indicative of future results.";

type Provider = "slack" | "discord" | "mattermost" | "teams" | "telegram";
const PROVIDERS: { id: Provider; label: string }[] = [
  { id: "slack", label: "Slack" },
  { id: "discord", label: "Discord" },
  { id: "mattermost", label: "Mattermost" },
  { id: "teams", label: "Teams" },
  { id: "telegram", label: "Telegram" },
];
const WEBHOOK_HINT: Record<Provider, string> = {
  slack: "Slack → Apps → Incoming Webhooks → add to a channel → copy the URL.",
  discord: "Discord → Server Settings → Integrations → Webhooks → New Webhook → Copy URL.",
  mattermost:
    "Mattermost → Integrations → Incoming Webhooks → Add → copy the URL. Self-hosted = fully your own server.",
  teams: "Teams → channel → Workflows / Connectors → Incoming Webhook → copy the URL.",
  telegram: "Telegram → message @BotFather → /newbot → paste the bot token and your chat ID.",
};

function money(n: number | null, sym: string): string {
  if (n == null) return "—";
  return `${sym}${n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function buildReportText(input: {
  equity: number | null;
  dayPL: number | null;
  decisions: number;
  fills: number;
  today: string;
  sample: boolean;
  sym: string;
}): string {
  const pl =
    input.dayPL == null ? "—" : `${input.dayPL >= 0 ? "+" : "-"}${money(Math.abs(input.dayPL), input.sym)}`;
  return [
    "autonomous_ — Daily Report",
    input.today + (input.sample ? "  (sample preview)" : ""),
    "",
    `Portfolio value   ${money(input.equity, input.sym)}`,
    `Today's P/L       ${pl}`,
    `Decisions today   ${input.decisions}`,
    `Fills today       ${input.fills}`,
    "",
    LEGAL_LINE,
    "Prepared locally on your device — sent only to the private channel you chose.",
  ].join("\n");
}

/**
 * Daily Report card (the Settings "Notifications" tab).
 *
 * Configures an AUTOMATIC daily report: a timer (1–3 times a day) generates a sober,
 * neutral summary of the user's OWN account and delivers it to the user's OWN PRIVATE
 * channels — a chat webhook (Slack / Discord / Mattermost / Teams / Telegram) and/or their
 * own email (SMTP). It is never posted publicly and never sent to a vendor server. The
 * timer fires while the app is running. Config lives in setup.json; the webhook URL and
 * SMTP password go to the OS keychain. Every report carries the legal notice.
 *
 * The whole configuration is shown fully expanded (no accordion, no per-channel on/off
 * toggle) — a channel is simply "active" once its details are filled in. It is a Senior
 * feature: Basic (Junior) sees the very same configuration greyed out and non-functional,
 * with an Upgrade call-to-action, so the value of upgrading is visible.
 */
export function DailyUpdatesCard() {
  const [scheduleTimes, setScheduleTimes] = useState<string[]>(["18:00"]);
  const [webhookEnabled, setWebhookEnabled] = useState(false);
  const [webhookProvider, setWebhookProvider] = useState<Provider>("slack");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [telegramToken, setTelegramToken] = useState("");
  const [telegramChatId, setTelegramChatId] = useState("");
  const [emailEnabled, setEmailEnabled] = useState(false);
  const [recipientEmail, setRecipientEmail] = useState("");
  const [smtpHost, setSmtpHost] = useState("");
  const [smtpPort, setSmtpPort] = useState("465");
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [save, setSave] = useState<{ saved: boolean; busy: boolean; error: string | null }>({
    saved: false,
    busy: false,
    error: null,
  });
  const [upgrading, setUpgrading] = useState(false);
  const [upgradeNote, setUpgradeNote] = useState<string | null>(null);

  const tier = useStore((s) => s.tier);
  const setEntitlement = useStore((s) => s.setEntitlement);
  const equity = useStore((s) => s.currentEquity);
  const reportSym = currencySymbol(useStore((s) => s.accountCurrency));
  const lastEquity = useStore((s) => s.lastEquity);
  const decisions = useStore((s) => s.roundTable.length);
  const fills = useStore((s) => s.activities.length);

  useEffect(() => {
    async function load() {
      if (!isDesktop()) return;
      try {
        const s = await getSetupState();
        if (Array.isArray(s.daily_report_times) && s.daily_report_times.length)
          setScheduleTimes((s.daily_report_times as string[]).slice(0, 3));
        if (s.daily_report_webhook_enabled != null) setWebhookEnabled(!!s.daily_report_webhook_enabled);
        if (s.daily_report_webhook_provider)
          setWebhookProvider(s.daily_report_webhook_provider as Provider);
        if (s.daily_report_telegram_chat_id)
          setTelegramChatId(s.daily_report_telegram_chat_id as string);
        if (s.daily_report_email_enabled != null) setEmailEnabled(!!s.daily_report_email_enabled);
        if (s.report_recipient_email) setRecipientEmail(s.report_recipient_email as string);
        if (s.smtp_host) setSmtpHost(s.smtp_host as string);
        if (s.smtp_port) setSmtpPort(String(s.smtp_port));
        if (s.smtp_user) setSmtpUser(s.smtp_user as string);
      } catch (e) {
        console.error("Failed to load daily-report config:", e);
      }
    }
    void load();
  }, []);

  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  // Senior (PRO) feature — Basic (Junior) sees the same config, greyed out + an upgrade CTA.
  const locked = tier !== "PRO";

  const onUpgrade = async () => {
    if (upgrading) return;
    setUpgrading(true);
    setUpgradeNote(null);
    try {
      const res = await claimBeta();
      if (res.status === "claimed") {
        const s = await fetchEntitlementStatus();
        if (s) setEntitlement(s.tier, s.can_upgrade, s.simulation_enabled ?? false, s.allow_live ?? false);
      } else {
        setUpgradeNote(
          res.error === "desktop-only"
            ? "Available in the desktop app."
            : "Upgrade failed — please try again.",
        );
      }
    } catch {
      setUpgradeNote("Upgrade failed — please try again.");
    } finally {
      setUpgrading(false);
    }
  };

  // In the browser/DEV preview there is no engine → seed a clearly-labelled sample
  // so the report format is visible. Live desktop uses real store data.
  const sample = !!import.meta.env.DEV && equity == null;
  const dayPL = equity != null && lastEquity != null ? equity - lastEquity : null;
  const today = new Date().toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  const reportText = sample
    ? buildReportText({ equity: 10250.4, dayPL: 128.75, decisions: 3, fills: 2, today, sample: true, sym: reportSym })
    : buildReportText({ equity, dayPL, decisions, fills, today, sample: false, sym: reportSym });

  // No per-channel on/off toggle: a channel is active once its details are present (or it
  // was previously saved as enabled — its secret lives in the keychain and is not re-shown).
  const webhookConfigured =
    webhookEnabled ||
    (webhookProvider === "telegram" ? telegramChatId.trim() !== "" : webhookUrl.trim() !== "");
  const emailConfigured = emailEnabled || recipientEmail.trim() !== "";
  const anyConfigured = webhookConfigured || emailConfigured;

  const setTimeAt = (i: number, v: string) =>
    setScheduleTimes((ts) => ts.map((t, j) => (j === i ? v : t)));
  const addTime = () => setScheduleTimes((ts) => (ts.length < 3 ? [...ts, "09:00"] : ts));
  const removeTime = (i: number) =>
    setScheduleTimes((ts) => (ts.length > 1 ? ts.filter((_, j) => j !== i) : ts));

  const handleSave = async () => {
    setSave({ saved: false, busy: true, error: null });
    try {
      await saveSetupState({
        daily_report_times: scheduleTimes,
        daily_report_webhook_enabled: webhookConfigured,
        daily_report_webhook_provider: webhookProvider,
        daily_report_email_enabled: emailConfigured,
        report_recipient_email: recipientEmail,
        daily_report_telegram_chat_id: telegramChatId,
        smtp_host: smtpHost,
        smtp_port: smtpPort,
        smtp_user: smtpUser,
      });
      if (webhookConfigured) {
        if (webhookProvider === "telegram") {
          if (telegramToken.trim()) await saveSecret("TELEGRAM_BOT_TOKEN", telegramToken.trim());
        } else if (webhookUrl.trim()) {
          await saveSecret("WEBHOOK_URL", webhookUrl.trim());
        }
      }
      if (emailConfigured && smtpPassword.trim()) await saveSecret("SMTP_PASSWORD", smtpPassword.trim());
      setSave({ saved: true, busy: false, error: null });
    } catch {
      setSave({ saved: false, busy: false, error: "Something went wrong while saving." });
    }
  };

  return (
    <div className="surface p-6">
      <div className="eyebrow mb-2">Daily Report</div>
      <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
        A sober daily summary of your own account activity, generated locally and delivered
        automatically to your own private channels — a chat app (Slack, Discord, Mattermost, Teams
        or Telegram) and/or your email. Nothing is posted publicly, and nothing is sent to autonomous_.
      </p>

      {/* Senior gate — Basic sees the config below greyed out, with this upgrade CTA. */}
      {locked && (
        <div className="surface-flat rounded-xl px-5 py-4 mb-5 flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-start gap-3">
            <IconLightbulb width={16} height={16} className="text-white/70 mt-0.5 shrink-0" />
            <div className="text-[12.5px] text-white/60 leading-relaxed max-w-md">
              <span className="font-semibold text-white/90">Automated daily reports are a Senior feature.</span>{" "}
              Upgrade to have autonomous_ deliver a private daily summary to your own channels — Slack,
              Discord, Mattermost, Teams, Telegram or email. Not investment advice.
            </div>
          </div>
          <div className="shrink-0">
            <button
              className="rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide text-white bg-[#00c27a] hover:bg-[#00d687] border-none transition-all transform active:scale-[0.97] disabled:opacity-60"
              onClick={() => void onUpgrade()}
              disabled={upgrading}
            >
              {upgrading ? "Upgrading…" : "Upgrade to Senior"}
            </button>
            {upgradeNote && <div className="text-[11px] text-[#ff8a80] mt-2 text-right">{upgradeNote}</div>}
          </div>
        </div>
      )}

      {/* The full configuration — fully expanded; greyed + inert for Basic. */}
      <div
        className={locked ? "opacity-50 pointer-events-none select-none" : ""}
        aria-disabled={locked || undefined}
      >
        {/* Schedule — up to 3 times a day */}
        <div className="pb-3 space-y-3">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-[13px] font-medium text-white/92">Send daily at</div>
              <div className="text-[11px] text-white/30 mt-0.5">
                Up to 3 times a day. Generated and delivered at each time, while the app is running.
              </div>
            </div>
            {scheduleTimes.length < 3 && (
              <button
                onClick={addTime}
                className="text-[11px] font-medium text-white/50 hover:text-white transition-colors shrink-0"
              >
                + Add time
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {scheduleTimes.map((t, i) => (
              <div key={i} className="flex items-center gap-1.5">
                <input
                  aria-label={`daily-report-time-${i}`}
                  type="time"
                  value={t}
                  onChange={(e) => setTimeAt(i, e.target.value)}
                  className="rounded-lg bg-black/40 border border-white/12 px-3 py-1.5 text-[13px] text-white/90 outline-none focus:border-white/30 num"
                />
                {scheduleTimes.length > 1 && (
                  <button
                    onClick={() => removeTime(i)}
                    aria-label={`remove-time-${i}`}
                    className="text-[15px] leading-none text-white/30 hover:text-white/70 transition-colors px-1"
                  >
                    ×
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>

        {/* Chat message (webhook) — recommended */}
        <div className="py-4 space-y-3 border-t border-white/5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[13px] font-medium text-white/92">Chat message — recommended</div>
              <div className="text-[11px] text-white/30 mt-0.5">
                Posts to one channel in your own workspace/server. No password — just a revocable webhook.
              </div>
            </div>
            <StatusDot tone={webhookConfigured ? "on" : "neutral"}>
              {webhookConfigured ? "Active" : "Off"}
            </StatusDot>
          </div>
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              {PROVIDERS.map((p) => {
                const active = webhookProvider === p.id;
                return (
                  <button
                    key={p.id}
                    onClick={() => setWebhookProvider(p.id)}
                    aria-label={`provider-${p.id}`}
                    className={`px-3 py-2 rounded-lg border text-[12px] font-medium transition-all ${
                      active
                        ? "border-white/20 bg-white/[0.06] text-white/90"
                        : "border-white/5 hover:border-white/10 bg-black/10 text-white/60"
                    }`}
                  >
                    {p.label}
                  </button>
                );
              })}
            </div>
            {webhookProvider === "telegram" ? (
              <div className="space-y-3">
                <label className="block text-[12px] text-white/55">
                  Bot token
                  <input
                    aria-label="telegram-token"
                    type="password"
                    value={telegramToken}
                    onChange={(e) => setTelegramToken(e.target.value)}
                    placeholder="123456:ABC-DEF…"
                    className={INPUT_CLASS}
                  />
                </label>
                <label className="block text-[12px] text-white/55">
                  Chat ID
                  <input
                    aria-label="telegram-chat-id"
                    type="text"
                    value={telegramChatId}
                    onChange={(e) => setTelegramChatId(e.target.value)}
                    placeholder="e.g. 987654321"
                    className={INPUT_CLASS}
                  />
                  <span className="block text-[10.5px] text-white/30 mt-1 leading-relaxed">
                    {WEBHOOK_HINT.telegram} Bot token stored in your OS keychain.
                  </span>
                </label>
              </div>
            ) : (
              <label className="block text-[12px] text-white/55">
                Incoming webhook URL
                <input
                  aria-label="webhook-url"
                  type="password"
                  value={webhookUrl}
                  onChange={(e) => setWebhookUrl(e.target.value)}
                  placeholder="https://…/webhook"
                  className={INPUT_CLASS}
                />
                <span className="block text-[10.5px] text-white/30 mt-1 leading-relaxed">
                  {WEBHOOK_HINT[webhookProvider]} Stored in your OS keychain.
                </span>
              </label>
            )}
          </div>
        </div>

        {/* Email (optional) */}
        <div className="py-4 space-y-3 border-t border-white/5">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-[13px] font-medium text-white/92">Email</div>
              <div className="text-[11px] text-white/30 mt-0.5">
                Sends through your own mail account (SMTP). Use an app password.
              </div>
            </div>
            <StatusDot tone={emailConfigured ? "on" : "neutral"}>
              {emailConfigured ? "Active" : "Off"}
            </StatusDot>
          </div>
          <div className="space-y-3">
            <label className="block text-[12px] text-white/55">
              Send to
              <input
                aria-label="report-recipient-email"
                type="email"
                value={recipientEmail}
                onChange={(e) => setRecipientEmail(e.target.value)}
                placeholder="you@example.com"
                className={INPUT_CLASS}
              />
            </label>
            <div className="grid grid-cols-3 gap-3">
              <label className="col-span-2 block text-[12px] text-white/55">
                SMTP server
                <input
                  aria-label="smtp-host"
                  type="text"
                  value={smtpHost}
                  onChange={(e) => setSmtpHost(e.target.value)}
                  placeholder="smtp.gmail.com"
                  className={INPUT_CLASS}
                />
              </label>
              <label className="block text-[12px] text-white/55">
                Port
                <input
                  aria-label="smtp-port"
                  type="text"
                  inputMode="numeric"
                  value={smtpPort}
                  onChange={(e) => setSmtpPort(e.target.value)}
                  placeholder="465"
                  className={INPUT_CLASS}
                />
              </label>
            </div>
            <label className="block text-[12px] text-white/55">
              Username
              <input
                aria-label="smtp-user"
                type="text"
                value={smtpUser}
                onChange={(e) => setSmtpUser(e.target.value)}
                placeholder="you@example.com"
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              App password
              <input
                aria-label="smtp-password"
                type="password"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
                className={INPUT_CLASS}
              />
              <span className="block text-[10.5px] text-white/30 mt-1 leading-relaxed">
                Gmail/Outlook require an app password (not your login). Stored in your OS keychain.
              </span>
            </label>
          </div>
        </div>

        {/* Preview */}
        <div className="py-4 space-y-2 border-t border-white/5">
          <div className="text-[13px] font-medium text-white/92">Report preview</div>
          <pre className="text-[11.5px] leading-relaxed text-white/70 bg-black/30 border border-white/10 rounded-lg px-3 py-3 whitespace-pre-wrap font-mono">
            {reportText}
          </pre>
        </div>

        {/* Save */}
        <div className="flex items-center gap-3 pt-1">
          <button
            className="btn"
            onClick={() => void handleSave()}
            disabled={locked || save.busy || !anyConfigured}
            style={{ opacity: locked || save.busy || !anyConfigured ? 0.4 : 1 }}
          >
            {save.busy ? "Saving…" : "Save configuration"}
          </button>
          {save.saved && <span className="text-[12px] text-white/60">Saved.</span>}
          {save.error && <span className="text-[12px] text-[#ff8a80]">{save.error}</span>}
        </div>
      </div>

      <div className="mt-4 pt-3 border-t border-white/5 text-[11px] text-white/40 leading-relaxed">
        {LEGAL_LINE} The report goes only to your own private channel — nothing is posted publicly,
        and nothing is sent to autonomous_.
      </div>
    </div>
  );
}
