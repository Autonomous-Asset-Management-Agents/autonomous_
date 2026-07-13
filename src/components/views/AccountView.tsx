import { motion } from "framer-motion";
import { useState, useEffect } from "react";
import { signOut, updateProfile } from "firebase/auth";
import { auth } from "@/lib/firebase";
import { useAuthState } from "@/components/useAuthState";
import { fetchRiskLimits, updateRiskLimits } from "@/lib/api";

const Toggle = ({ on, onToggle }: { on: boolean; onToggle: () => void }) => (
  <button className={"aa-toggle" + (on ? " on" : "")} onClick={onToggle} aria-label="toggle" />
);

export const AccountView = () => {
  const { user } = useAuthState();
  const [maxDrawdown, setMaxDrawdown] = useState("5");
  const [maxPosition, setMaxPosition] = useState("20");
  const [saving, setSaving] = useState(false);
  const [displayName, setDisplayName] = useState(user?.displayName ?? "");
  const [editingName, setEditingName] = useState(false);
  const [notifications, setNotifications] = useState({
    trades: true,
    risk: true,
    daily: true,
    downtime: false,
  });
  const [autoTrading, setAutoTrading] = useState(true);

  useEffect(() => {
    fetchRiskLimits().then((res) => {
      if (res?.status === "success") {
        if (res.risk_limits?.max_daily_drawdown_pct) setMaxDrawdown(res.risk_limits.max_daily_drawdown_pct.toString());
        if (res.risk_limits?.max_position_size_pct)  setMaxPosition(res.risk_limits.max_position_size_pct.toString());
      }
    });
  }, []);

  const handleSaveLimits = async () => {
    setSaving(true);
    await updateRiskLimits({
      max_daily_drawdown_pct: parseFloat(maxDrawdown),
      max_position_size_pct: parseFloat(maxPosition),
    });
    setSaving(false);
  };

  const handleSaveName = async () => {
    if (!user) return;
    await updateProfile(user, { displayName });
    setEditingName(false);
  };

  const handleLogout = () => signOut(auth);

  const toggle = (key: keyof typeof notifications) =>
    setNotifications((n) => ({ ...n, [key]: !n[key] }));

  const sectionTitle = (label: string) => (
    <div style={{ fontSize: 12, fontWeight: 600, color: "#d4a853", textTransform: "uppercase", letterSpacing: "0.05em", padding: "14px 0 8px" }}>
      {label}
    </div>
  );

  const row = (label: string, right: React.ReactNode) => (
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px 0", borderBottom: "1px solid rgba(255,255,255,0.05)" }}>
      <span style={{ fontSize: 15, color: "rgba(255,255,255,0.85)" }}>{label}</span>
      {right}
    </div>
  );



  const valStyle: React.CSSProperties = { fontSize: 14, fontFamily: "JetBrains Mono, monospace", color: "rgba(255,255,255,0.55)", fontWeight: 500 };
  const linkStyle: React.CSSProperties = { ...valStyle, color: "#d4a853", cursor: "pointer" };

  const inputSt: React.CSSProperties = {
    width: "100px", padding: "5px 10px",
    background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.08)",
    borderRadius: 8, color: "rgba(255,255,255,0.85)", fontSize: 13,
    fontFamily: "JetBrains Mono, monospace", outline: "none",
  };

  return (
    <motion.div
      initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
      className="min-h-screen pt-16 pb-12 px-6"
      style={{ maxWidth: 540, margin: "0 auto" }}
    >
      <div className="pt-10 pb-6">
        <div className="dash-overline">Settings</div>
        <h2 className="dash-title">My Account</h2>
      </div>

      {/* Profile */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 }} className="surface-card px-5 mb-2.5">
        {sectionTitle("Profile")}
        {row("Name",
          editingName ? (
            <div className="flex items-center gap-2">
              <input
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                style={{ ...inputSt, width: 140 }}
                onKeyDown={(e) => e.key === "Enter" && handleSaveName()}
              />
              <button onClick={handleSaveName} style={{ ...linkStyle, fontSize: 12 }}>Save</button>
              <button onClick={() => setEditingName(false)} style={{ ...valStyle, fontSize: 12, cursor: "pointer" }}>Cancel</button>
            </div>
          ) : (
            <button onClick={() => setEditingName(true)} style={linkStyle}>
              {user?.displayName ?? "—"}
            </button>
          )
        )}
        {row("Email",   <span style={valStyle}>{user?.email ?? "—"}</span>)}
        {row("Plan",    <span style={valStyle}>Private Beta</span>)}
        {row("Region",  <span style={valStyle}>DACH</span>)}
        {row("UID",     <span style={{ ...valStyle, fontSize: 10 }}>{user?.uid?.slice(0, 16) ?? "—"}…</span>)}
      </motion.div>

      {/* Trading */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.15 }} className="surface-card px-5 mb-2.5">
        {sectionTitle("Trading")}
        {row("Max Drawdown (%)",
          <input type="number" value={maxDrawdown} onChange={(e) => setMaxDrawdown(e.target.value)} min="1" max="50" style={inputSt} />
        )}
        {row("Max Position (%)",
          <input type="number" value={maxPosition} onChange={(e) => setMaxPosition(e.target.value)} min="1" max="100" style={inputSt} />
        )}
        {row("Auto-Trading", <Toggle on={autoTrading} onToggle={() => setAutoTrading(!autoTrading)} />)}
        <div style={{ padding: "12px 0" }}>
          <button
            onClick={handleSaveLimits}
            disabled={saving}
            style={{
              padding: "7px 16px", borderRadius: 8, fontSize: 12, fontWeight: 600,
              background: "rgba(212,168,83,0.08)", border: "1px solid rgba(212,168,83,0.2)",
              color: "#d4a853", cursor: saving ? "not-allowed" : "pointer", transition: "all 0.2s",
            }}
          >
            {saving ? "Saving…" : "Save Configuration"}
          </button>
        </div>
      </motion.div>

      {/* Notifications */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.2 }} className="surface-card px-5 mb-2.5">
        {sectionTitle("Notifications")}
        {row("Trade Executions", <Toggle on={notifications.trades}   onToggle={() => toggle("trades")} />)}
        {row("Risk Alerts",      <Toggle on={notifications.risk}     onToggle={() => toggle("risk")} />)}
        {row("Daily Summary",    <Toggle on={notifications.daily}    onToggle={() => toggle("daily")} />)}
        {row("Agent Downtime",   <Toggle on={notifications.downtime} onToggle={() => toggle("downtime")} />)}
      </motion.div>

      {/* Security */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.25 }} className="surface-card px-5 mb-2.5">
        {sectionTitle("Security")}
        {row("Auth Provider", <span style={valStyle}>{user?.providerData?.[0]?.providerId ?? "—"}</span>)}
        {row("API Keys",      <button style={linkStyle}>Manage</button>)}
        {row("Broker",        <span style={valStyle}>Alpaca (Paper)</span>)}
        {row("Two-Factor",    <Toggle on={true} onToggle={() => {}} />)}
      </motion.div>

      {/* Danger zone */}
      <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.3 }} style={{ textAlign: "center", marginTop: 28 }}>
        <button
          onClick={handleLogout}
          style={{ fontSize: 13, fontWeight: 500, color: "rgba(255,69,58,0.7)", background: "none", border: "none", cursor: "pointer", padding: "8px 20px", transition: "opacity 0.2s" }}
        >
          Sign Out
        </button>
      </motion.div>
    </motion.div>
  );
};
