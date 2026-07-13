import { useState, useEffect } from "react";
import { saveSecret, isDesktop, saveSetupState, getSetupState } from "@/lib/desktopBridge";

const INPUT_CLASS =
  "mt-1 w-full rounded-lg bg-black/40 border border-white/12 px-3 py-2 text-[13px] text-white/90 outline-none focus:border-white/30 disabled:opacity-40";

interface SavedState {
  saved: boolean;
  error: string | null;
  busy: boolean;
}

export function DailyUpdatesCard() {
  const [isExpanded, setIsExpanded] = useState(false);
  const [selectedChannels, setSelectedChannels] = useState<string[]>([]);
  const [postingTime, setPostingTime] = useState<string>("16:05");

  // Credential states
  const [xApiKey, setXApiKey] = useState("");
  const [xApiSecret, setXApiSecret] = useState("");
  const [xAccessToken, setXAccessToken] = useState("");
  const [xAccessTokenSecret, setXAccessTokenSecret] = useState("");
  const [xStatus, setXStatus] = useState<SavedState>({ saved: false, error: null, busy: false });

  const [igAccessToken, setIgAccessToken] = useState("");
  const [igPageId, setIgPageId] = useState("");
  const [igStatus, setIgStatus] = useState<SavedState>({ saved: false, error: null, busy: false });

  const [ytChannelId, setYtChannelId] = useState("");
  const [ytAccessToken, setYtAccessToken] = useState("");
  const [ytStatus, setYtStatus] = useState<SavedState>({ saved: false, error: null, busy: false });

  const [liAuthorUrn, setLiAuthorUrn] = useState("");
  const [liAccessToken, setLiAccessToken] = useState("");
  const [liStatus, setLiStatus] = useState<SavedState>({ saved: false, error: null, busy: false });

  useEffect(() => {
    async function loadConfig() {
      if (isDesktop()) {
        try {
          const state = await getSetupState();
          if (state.daily_updates_channels) {
            setSelectedChannels(state.daily_updates_channels as string[]);
          }
          if (state.daily_updates_posting_time) {
            setPostingTime(state.daily_updates_posting_time as string);
          }
        } catch (e) {
          console.error("Failed to load setup state for daily updates:", e);
        }
      }
    }
    void loadConfig();
  }, []);

  const isTest = typeof process !== "undefined" && process.env.NODE_ENV === "test";
  if (!isDesktop() && !(!!import.meta.env.DEV && !isTest)) return null;

  const handleChannelToggle = (channel: string) => {
    const next = selectedChannels.includes(channel)
      ? selectedChannels.filter((c) => c !== channel)
      : [...selectedChannels, channel];
    setSelectedChannels(next);
    if (isDesktop()) {
      void saveSetupState({ daily_updates_channels: next });
    }
  };

  const handleTimeChange = (time: string) => {
    setPostingTime(time);
    if (isDesktop()) {
      void saveSetupState({ daily_updates_posting_time: time });
    }
  };

  const saveX = async () => {
    if (!xApiKey.trim() || !xApiSecret.trim() || !xAccessToken.trim() || !xAccessTokenSecret.trim()) return;
    setXStatus({ saved: false, error: null, busy: true });
    try {
      const a = await saveSecret("X_API_KEY", xApiKey.trim());
      const b = await saveSecret("X_API_SECRET", xApiSecret.trim());
      const c = await saveSecret("X_ACCESS_TOKEN", xAccessToken.trim());
      const d = await saveSecret("X_ACCESS_TOKEN_SECRET", xAccessTokenSecret.trim());
      if (a.ok && b.ok && c.ok && d.ok) {
        setXStatus({ saved: true, error: null, busy: false });
        setXApiKey("");
        setXApiSecret("");
        setXAccessToken("");
        setXAccessTokenSecret("");
      } else {
        setXStatus({ saved: false, error: "Failed to save X credentials to keychain.", busy: false });
      }
    } catch {
      setXStatus({ saved: false, error: "Something went wrong.", busy: false });
    }
  };

  const saveInstagram = async () => {
    if (!igAccessToken.trim() || !igPageId.trim()) return;
    setIgStatus({ saved: false, error: null, busy: true });
    try {
      const a = await saveSecret("INSTAGRAM_ACCESS_TOKEN", igAccessToken.trim());
      const b = await saveSecret("INSTAGRAM_PAGE_ID", igPageId.trim());
      if (a.ok && b.ok) {
        setIgStatus({ saved: true, error: null, busy: false });
        setIgAccessToken("");
        setIgPageId("");
      } else {
        setIgStatus({ saved: false, error: "Failed to save Instagram credentials to keychain.", busy: false });
      }
    } catch {
      setIgStatus({ saved: false, error: "Something went wrong.", busy: false });
    }
  };

  const saveYouTube = async () => {
    if (!ytChannelId.trim() || !ytAccessToken.trim()) return;
    setYtStatus({ saved: false, error: null, busy: true });
    try {
      const a = await saveSecret("YOUTUBE_CHANNEL_ID", ytChannelId.trim());
      const b = await saveSecret("YOUTUBE_ACCESS_TOKEN", ytAccessToken.trim());
      if (a.ok && b.ok) {
        setYtStatus({ saved: true, error: null, busy: false });
        setYtChannelId("");
        setYtAccessToken("");
      } else {
        setYtStatus({ saved: false, error: "Failed to save YouTube credentials to keychain.", busy: false });
      }
    } catch {
      setYtStatus({ saved: false, error: "Something went wrong.", busy: false });
    }
  };

  const saveLinkedIn = async () => {
    if (!liAuthorUrn.trim() || !liAccessToken.trim()) return;
    setLiStatus({ saved: false, error: null, busy: true });
    try {
      const a = await saveSecret("LINKEDIN_AUTHOR_URN", liAuthorUrn.trim());
      const b = await saveSecret("LINKEDIN_ACCESS_TOKEN", liAccessToken.trim());
      if (a.ok && b.ok) {
        setLiStatus({ saved: true, error: null, busy: false });
        setLiAuthorUrn("");
        setLiAccessToken("");
      } else {
        setLiStatus({ saved: false, error: "Failed to save LinkedIn credentials to keychain.", busy: false });
      }
    } catch {
      setLiStatus({ saved: false, error: "Something went wrong.", busy: false });
    }
  };

  return (
    <div className="surface p-6">
      <div className="flex items-center justify-between mb-2">
        <div className="eyebrow">Daily Updates</div>
        <button 
          onClick={() => setIsExpanded(!isExpanded)}
          className="text-[11px] font-medium text-white/50 hover:text-white transition-colors"
        >
          {isExpanded ? "Close" : "Manage"}
        </button>
      </div>
      
      {!isExpanded ? (
        <div className="text-[12px] text-white/40">
          {selectedChannels.length > 0 
            ? `Configured to post to ${selectedChannels.length} channel(s) at ${postingTime}. `
            : "No channels configured for daily updates. "}
          Click manage to configure.
        </div>
      ) : (
        <>
          <p className="text-[11px] text-white/30 mb-4 max-w-md leading-relaxed">
            Configure automated social media postings for daily recap videos. Secret keys and access tokens are securely persisted in your OS keychain.
          </p>

      {/* Posting Time Selection */}
      <div className="flex items-center gap-4 py-3 border-b border-white/5">
        <div className="flex-1">
          <div className="text-[13px] font-medium text-white/92">Posting Time</div>
          <div className="text-[11px] text-white/30 mt-0.5">Specify when the daily recap should be generated and posted.</div>
        </div>
        <input
          aria-label="posting-time-input"
          type="time"
          value={postingTime}
          onChange={(e) => handleTimeChange(e.target.value)}
          className="rounded-lg bg-black/40 border border-white/12 px-3 py-1.5 text-[13px] text-white/90 outline-none focus:border-white/30 num"
        />
      </div>

      {/* Target Channels Checkboxes */}
      <div className="py-4 space-y-3">
        <div className="text-[13px] font-medium text-white/92">Target Channels</div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { id: "x", label: "X (Twitter)" },
            { id: "instagram", label: "Instagram" },
            { id: "youtube", label: "YouTube Shorts" },
            { id: "linkedin", label: "LinkedIn" },
          ].map((ch) => {
            const checked = selectedChannels.includes(ch.id);
            return (
              <label
                key={ch.id}
                className={`flex items-center gap-2 p-3 rounded-lg border transition-all cursor-pointer select-none ${
                  checked
                    ? "border-white/20 bg-white/[0.04]"
                    : "border-white/5 hover:border-white/10 bg-black/10"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => handleChannelToggle(ch.id)}
                  aria-label={`${ch.id}-checkbox`}
                  className="rounded border-white/20 bg-black/40 text-bull focus:ring-0 w-3.5 h-3.5"
                />
                <span className="text-[12px] text-white/80">{ch.label}</span>
              </label>
            );
          })}
        </div>
      </div>

      {/* Expandable Platform Credentials Form Fields */}
      <div className="mt-4 space-y-4">
        {/* X (Twitter) Credentials */}
        {selectedChannels.includes("x") && (
          <div className="p-4 rounded-xl border border-white/5 bg-black/20 space-y-3">
            <div className="text-[12px] font-semibold text-white/80">X (Twitter) Credentials</div>
            {xStatus.error && (
              <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
                {xStatus.error}
              </div>
            )}
            {xStatus.saved && (
              <div className="text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
                Saved to the keychain.
              </div>
            )}
            <label className="block text-[12px] text-white/55">
              API Key (Consumer Key)
              <input
                aria-label="x-api-key"
                type="password"
                value={xApiKey}
                onChange={(e) => setXApiKey(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              API Secret (Consumer Secret)
              <input
                aria-label="x-api-secret"
                type="password"
                value={xApiSecret}
                onChange={(e) => setXApiSecret(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              Access Token
              <input
                aria-label="x-access-token"
                type="password"
                value={xAccessToken}
                onChange={(e) => setXAccessToken(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              Access Token Secret
              <input
                aria-label="x-access-token-secret"
                type="password"
                value={xAccessTokenSecret}
                onChange={(e) => setXAccessTokenSecret(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <button
              className="btn"
              onClick={() => void saveX()}
              disabled={xStatus.busy || !xApiKey.trim() || !xApiSecret.trim() || !xAccessToken.trim() || !xAccessTokenSecret.trim()}
              style={{ opacity: (!xApiKey.trim() || !xApiSecret.trim() || !xAccessToken.trim() || !xAccessTokenSecret.trim() || xStatus.busy) ? 0.4 : 1 }}
            >
              {xStatus.busy ? "Saving…" : "Save X credentials"}
            </button>
          </div>
        )}

        {/* Instagram Credentials */}
        {selectedChannels.includes("instagram") && (
          <div className="p-4 rounded-xl border border-white/5 bg-black/20 space-y-3">
            <div className="text-[12px] font-semibold text-white/80">Instagram Credentials</div>
            {igStatus.error && (
              <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
                {igStatus.error}
              </div>
            )}
            {igStatus.saved && (
              <div className="text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
                Saved to the keychain.
              </div>
            )}
            <label className="block text-[12px] text-white/55">
              Access Token
              <input
                aria-label="instagram-access-token"
                type="password"
                value={igAccessToken}
                onChange={(e) => setIgAccessToken(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              Page ID
              <input
                aria-label="instagram-page-id"
                type="text"
                value={igPageId}
                onChange={(e) => setIgPageId(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <button
              className="btn"
              onClick={() => void saveInstagram()}
              disabled={igStatus.busy || !igAccessToken.trim() || !igPageId.trim()}
              style={{ opacity: (!igAccessToken.trim() || !igPageId.trim() || igStatus.busy) ? 0.4 : 1 }}
            >
              {igStatus.busy ? "Saving…" : "Save Instagram credentials"}
            </button>
          </div>
        )}

        {/* YouTube Credentials */}
        {selectedChannels.includes("youtube") && (
          <div className="p-4 rounded-xl border border-white/5 bg-black/20 space-y-3">
            <div className="text-[12px] font-semibold text-white/80">YouTube Shorts Credentials</div>
            <p className="text-[10px] text-white/35 leading-relaxed">
              Hinweis: Das Hochladen von Videos erfordert eine OAuth-Berechtigung. Beim ersten Skriptlauf wird automatisch ein lokales Browser-Login-Fenster geöffnet, um deinen Google-Account zu autorisieren. Die hier gespeicherten Werte dienen als optionale Fallback-Daten.
            </p>
            {ytStatus.error && (
              <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
                {ytStatus.error}
              </div>
            )}
            {ytStatus.saved && (
              <div className="text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
                Saved to the keychain.
              </div>
            )}
            <label className="block text-[12px] text-white/55">
              Channel ID
              <input
                aria-label="youtube-channel-id"
                type="text"
                value={ytChannelId}
                onChange={(e) => setYtChannelId(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              Access Token
              <input
                aria-label="youtube-access-token"
                type="password"
                value={ytAccessToken}
                onChange={(e) => setYtAccessToken(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <button
              className="btn"
              onClick={() => void saveYouTube()}
              disabled={ytStatus.busy || !ytChannelId.trim() || !ytAccessToken.trim()}
              style={{ opacity: (!ytChannelId.trim() || !ytAccessToken.trim() || ytStatus.busy) ? 0.4 : 1 }}
            >
              {ytStatus.busy ? "Saving…" : "Save YouTube credentials"}
            </button>
          </div>
        )}

        {/* LinkedIn Credentials */}
        {selectedChannels.includes("linkedin") && (
          <div className="p-4 rounded-xl border border-white/5 bg-black/20 space-y-3">
            <div className="text-[12px] font-semibold text-white/80">LinkedIn Credentials</div>
            <p className="text-[10px] text-white/35 leading-relaxed">
              Hinweis: Das Teilen auf LinkedIn erfordert ein OAuth-Access-Token. Die Author-URN ist optional — wenn du sie leer lässt, fragt das System deine ID automatisch über die API ab.
            </p>
            {liStatus.error && (
              <div className="text-[12px] text-[#ff8a80] bg-[#ff453a]/10 border border-[#ff453a]/25 rounded-lg px-3 py-2">
                {liStatus.error}
              </div>
            )}
            {liStatus.saved && (
              <div className="text-[12px] text-white/70 bg-white/[0.05] border border-white/10 rounded-lg px-3 py-2">
                Saved to the keychain.
              </div>
            )}
            <label className="block text-[12px] text-white/55">
              Author URN
              <input
                aria-label="linkedin-author-urn"
                type="text"
                placeholder="urn:li:person:abcde"
                value={liAuthorUrn}
                onChange={(e) => setLiAuthorUrn(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <label className="block text-[12px] text-white/55">
              Access Token
              <input
                aria-label="linkedin-access-token"
                type="password"
                value={liAccessToken}
                onChange={(e) => setLiAccessToken(e.target.value)}
                className={INPUT_CLASS}
              />
            </label>
            <button
              className="btn"
              onClick={() => void saveLinkedIn()}
              disabled={liStatus.busy || !liAuthorUrn.trim() || !liAccessToken.trim()}
              style={{ opacity: (!liAuthorUrn.trim() || !liAccessToken.trim() || liStatus.busy) ? 0.4 : 1 }}
            >
              {liStatus.busy ? "Saving…" : "Save LinkedIn credentials"}
            </button>
          </div>
        )}
      </div>
        </>
      )}
    </div>
  );
}
