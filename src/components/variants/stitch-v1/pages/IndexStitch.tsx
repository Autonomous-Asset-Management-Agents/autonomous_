import { useIndexData } from "@/hooks/useIndexData";

// Material Symbols helper – kommt via Google Fonts in index.html
const Icon = ({ name, filled = false, className = "" }: { name: string; filled?: boolean; className?: string }) => (
  <span
    className={`material-symbols-outlined ${className}`}
    style={{ fontVariationSettings: `'FILL' ${filled ? 1 : 0}, 'wght' 400, 'GRAD' 0, 'opsz' 24` }}
  >
    {name}
  </span>
);

const IndexStitch = () => {
  const {
    user,
    portfolioData,
    currentView,
    handleNavigate,
    handleChatToggle,
  } = useIndexData();

  const equity = portfolioData?.status === "success" ? portfolioData.equity : null;
  const lastEquity = portfolioData?.status === "success" ? portfolioData.last_equity : null;
  const positions = portfolioData?.status === "success" ? portfolioData.positions ?? [] : [];
  const isConnected = portfolioData?.status === "success";

  // Berechne Monats-Performance aus equity und last_equity
  const monthlyGainAbs = equity != null && lastEquity != null ? equity - lastEquity : null;
  const monthlyGainPct = equity != null && lastEquity != null && lastEquity > 0
    ? ((equity - lastEquity) / lastEquity) * 100
    : null;

  // Gesamt PnL aus allen Positionen summieren
  const totalUnrealizedPnl = positions.reduce((acc, p) => acc + (p.unrealized_pnl ?? 0), 0);
  const totalUnrealizedPnlPct = equity != null && equity > 0
    ? (totalUnrealizedPnl / equity) * 100
    : null;

  return (
    <div
      className="min-h-screen font-body selection:bg-stitch-primary selection:text-stitch-text"
      style={{ backgroundColor: "#F3F4F6", color: "#111111" }}
    >
      {/* TopAppBar */}
      <header className="bg-white/80 backdrop-blur-md sticky top-0 z-50 w-full border-b border-stitch-border">
        <div className="flex justify-between items-center px-6 py-4 w-full max-w-7xl mx-auto">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full overflow-hidden bg-stitch-primary flex items-center justify-center">
              {user?.photoURL ? (
                <img alt="Profilbild" className="w-full h-full object-cover" src={user.photoURL} />
              ) : (
                <Icon name="person" className="text-stitch-text" />
              )}
            </div>
            <span className="font-headline font-extrabold text-xl tracking-tighter text-stitch-text">
              InvestAI
            </span>
          </div>
          <div className="flex items-center gap-4">
            <button
              onClick={handleChatToggle}
              className="text-stitch-text hover:opacity-80 transition-opacity active:scale-95 p-2"
              aria-label="Benachrichtigungen"
            >
              <Icon name="notifications" />
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 pt-12 pb-32">
        {/* Hero: Net Worth */}
        <section className="mb-16">
          <div className="flex flex-col md:flex-row justify-between items-end gap-8">
            <div className="space-y-2">
              <h2 className="font-label text-xs uppercase tracking-[0.2em] text-stitch-text/60">
                Total Net Worth
              </h2>
              <p className="font-headline font-extrabold text-6xl md:text-7xl tracking-tighter text-stitch-text">
                {equity != null
                  ? `$${equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                  : isConnected === false
                  ? "N/A"
                  : "Laden…"}
              </p>
              {monthlyGainPct != null && (
                <div className={`flex items-center gap-2 ${monthlyGainPct >= 0 ? "text-green-600" : "text-red-500"}`}>
                  <Icon name={monthlyGainPct >= 0 ? "trending_up" : "trending_down"} filled className="text-sm" />
                  <span className="font-bold text-sm">
                    {monthlyGainPct >= 0 ? "+" : ""}
                    {monthlyGainPct.toFixed(2)}% this month
                  </span>
                </div>
              )}
            </div>

            {/* Sparkline (stilisiert) */}
            <div className="w-full md:w-1/2 h-24 relative overflow-hidden">
              <svg
                className="w-full h-full fill-none"
                viewBox="0 0 400 100"
                preserveAspectRatio="none"
              >
                <path
                  d="M0,80 Q50,75 100,60 T200,50 T300,30 T400,10"
                  stroke="#FF5C34"
                  strokeLinecap="round"
                  strokeWidth="3"
                />
                <path
                  d="M0,80 Q50,75 100,60 T200,50 T300,30 T400,10 V100 H0 Z"
                  fill="rgba(255,92,52,0.1)"
                />
              </svg>
            </div>
          </div>
        </section>

        {/* Quick Actions */}
        <section className="flex gap-4 mb-16">
          <button
            onClick={() => handleNavigate("dashboard")}
            className="flex-1 font-headline font-bold py-4 rounded-[16px] hover:brightness-110 transition-all active:scale-95 flex items-center justify-center gap-2"
            style={{ backgroundColor: "#FF5C34", color: "#fff" }}
          >
            <Icon name="bar_chart" className="text-xl" />
            <span>Dashboard</span>
          </button>
          <button
            onClick={() => handleNavigate("account")}
            className="flex-1 font-headline font-bold py-4 rounded-[16px] hover:brightness-110 transition-all active:scale-95 flex items-center justify-center gap-2"
            style={{ backgroundColor: "#FF5C34", color: "#fff" }}
          >
            <Icon name="account_balance_wallet" className="text-xl" />
            <span>Account</span>
          </button>
        </section>

        {/* Bento Grid */}
        <section className="grid grid-cols-1 md:grid-cols-12 gap-6">
          <div className="md:col-span-12 mb-4">
            <h3 className="font-headline text-2xl font-bold tracking-tight text-stitch-text flex items-center gap-3">
              <Icon name="auto_awesome" filled className="text-stitch-action" style={{ color: "#FF5C34" }} />
              Dashboard Overview
            </h3>
          </div>

          {/* AI Alert Card */}
          <div className="md:col-span-7 bg-white border border-stitch-border rounded-[16px] p-8 hover:bg-white/50 transition-colors group">
            <div className="flex justify-between items-start mb-6">
              <span className="bg-stitch-primary text-stitch-text px-4 py-1.5 rounded-full text-xs font-bold uppercase tracking-widest border border-stitch-border">
                Protect
              </span>
              <Icon name="info" className="text-stitch-text/40 group-hover:text-stitch-text transition-colors" />
            </div>
            <h4 className="font-headline text-2xl font-bold text-stitch-text mb-4">Market Volatility Alert</h4>
            <p className="text-stitch-text/80 leading-relaxed text-lg mb-8">
              High inflation signals detected in regional markets. Our AI suggests reallocating 5% of your
              high-risk tech holdings into defensive utilities to hedge against short-term correction.
            </p>
            <div className="bg-stitch-primary/30 w-full h-2 rounded-full overflow-hidden">
              <div className="h-full w-[85%]" style={{ backgroundColor: "#FF5C34" }} />
            </div>
            <div className="mt-2 flex justify-between text-[10px] text-stitch-text/60 font-bold uppercase tracking-widest">
              <span>Protection Strength</span>
              <span>85% Secure</span>
            </div>
          </div>

          {/* Portfolio Summary Card */}
          <div className="md:col-span-5 bg-white border border-stitch-border rounded-[16px] p-8 hover:bg-white/50 transition-colors group">
            <div className="flex justify-between items-start mb-6">
              <span
                className="px-4 py-1.5 rounded-full text-xs font-bold uppercase tracking-widest border"
                style={{ backgroundColor: "rgba(255,92,52,0.1)", color: "#FF5C34", borderColor: "rgba(255,92,52,0.2)" }}
              >
                Active Assets
              </span>
              <Icon name="pie_chart" className="text-stitch-text/40 group-hover:text-stitch-text transition-colors" />
            </div>
            <h4 className="font-headline text-2xl font-bold text-stitch-text mb-4">Current Portfolio</h4>
            <div className="space-y-6">
              <div>
                <p className="text-[10px] font-bold text-stitch-text/50 uppercase tracking-[0.2em] mb-1">Total Holdings</p>
                <div className="flex items-baseline gap-2">
                  <span className="text-3xl font-headline font-extrabold text-stitch-text">
                    {positions.length > 0 ? positions.length : "—"}
                  </span>
                  <span className="text-sm text-stitch-text/60 font-medium">Assets</span>
                </div>
              </div>
              <div>
                <p className="text-[10px] font-bold text-stitch-text/50 uppercase tracking-[0.2em] mb-1">
                  Life-time Performance
                </p>
                <div className="flex items-baseline gap-2">
                  <span
                    className={`text-3xl font-headline font-extrabold ${
                      totalUnrealizedPnl >= 0 ? "text-green-600" : "text-red-500"
                    }`}
                  >
                    {totalUnrealizedPnl >= 0 ? "+" : ""}$
                    {Math.abs(totalUnrealizedPnl).toLocaleString("en-US", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}
                  </span>
                  {totalUnrealizedPnlPct != null && (
                    <span
                      className={`text-sm font-bold ${
                        totalUnrealizedPnlPct >= 0 ? "text-green-600" : "text-red-500"
                      }`}
                    >
                      ({totalUnrealizedPnlPct >= 0 ? "+" : ""}
                      {totalUnrealizedPnlPct.toFixed(1)}%)
                    </span>
                  )}
                </div>
              </div>
            </div>
            <button
              onClick={() => handleNavigate("dashboard")}
              className="w-full mt-8 py-3 rounded-full font-bold transition-all"
              style={{
                border: "1px solid rgba(255,92,52,0.4)",
                color: "#FF5C34",
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLButtonElement).style.backgroundColor = "#FF5C34";
                (e.currentTarget as HTMLButtonElement).style.color = "#fff";
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLButtonElement).style.backgroundColor = "transparent";
                (e.currentTarget as HTMLButtonElement).style.color = "#FF5C34";
              }}
            >
              View All Holdings
            </button>
          </div>

          {/* Live Positions – erste 3 aus dem Portfolio */}
          {positions.length > 0
            ? positions.slice(0, 3).map((pos) => (
                <div key={pos.symbol} className="md:col-span-4 bg-white border border-stitch-border rounded-[16px] p-6">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <div
                        className="w-8 h-8 rounded-md flex items-center justify-center text-[10px] font-bold"
                        style={{ backgroundColor: "#D7EFFF", color: "#111" }}
                      >
                        {pos.symbol.slice(0, 4)}
                      </div>
                      <p className="text-[10px] font-bold text-stitch-text/50 uppercase tracking-[0.2em]">
                        {pos.symbol}
                      </p>
                    </div>
                    <span
                      className={`text-xs font-bold ${
                        (pos.unrealized_pnl_pct ?? 0) >= 0 ? "text-green-600" : "text-red-500"
                      }`}
                    >
                      {(pos.unrealized_pnl_pct ?? 0) >= 0 ? "+" : ""}
                      {((pos.unrealized_pnl_pct ?? 0) * 100).toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex items-center justify-between">
                    <span className="text-2xl font-headline font-bold text-stitch-text">
                      ${Number(pos.market_value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </span>
                    <span className="text-stitch-text/40 text-[10px] font-bold">
                      {pos.qty} SHARES
                    </span>
                  </div>
                </div>
              ))
            : /* Fallback: Skeleton-Karten wenn noch keine Positionen geladen */
              ["—", "—", "—"].map((_, i) => (
                <div key={i} className="md:col-span-4 bg-white border border-stitch-border rounded-[16px] p-6 animate-pulse">
                  <div className="h-4 bg-stitch-border rounded-full mb-4 w-2/3" />
                  <div className="h-8 bg-stitch-border rounded-full w-1/2" />
                </div>
              ))}
        </section>
      </main>

      {/* Bottom Navigation */}
      <nav className="fixed bottom-0 left-0 w-full z-50 flex justify-around items-center px-4 pb-8 pt-4 bg-white/90 backdrop-blur-md border-t border-stitch-border rounded-t-3xl">
        {[
          { id: "home", icon: "home", label: "Home" },
          { id: "dashboard", icon: "swap_horiz", label: "Trade" },
          { id: "dashboard", icon: "smart_toy", label: "Coach" },
          { id: "account", icon: "person", label: "Profile" },
        ].map((item, idx) => {
          const isActive = currentView === item.id && idx === 0;
          return (
            <button
              key={idx}
              onClick={() => handleNavigate(item.id)}
              className={`flex flex-col items-center justify-center px-5 py-2 rounded-full transition-all duration-200 active:scale-90 ${
                isActive ? "bg-stitch-primary text-stitch-text" : "text-stitch-text/50 hover:text-stitch-text"
              }`}
            >
              <Icon name={item.icon} filled={isActive} />
              <span className="font-label font-bold text-[10px] uppercase tracking-wider mt-1">
                {item.label}
              </span>
            </button>
          );
        })}
      </nav>
    </div>
  );
};

export default IndexStitch;
