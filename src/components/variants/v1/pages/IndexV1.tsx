import { motion, AnimatePresence } from "framer-motion";
import { Header } from "@/components/Header";
import { ChartBackground } from "@/components/ChartBackground";
import { ChatPanel } from "@/components/ChatPanel";
import { ContactPanel } from "@/components/ContactPanel";
import { LandingView } from "@/components/views/LandingView";
import { DashboardView } from "@/components/views/DashboardView";
import { AccountView } from "@/components/views/AccountView";
import { StockDetailView } from "@/components/views/StockDetailView";
import { useIndexData } from "@/hooks/useIndexData";

const IndexV1 = () => {
  const {
    isChatOpen, setIsChatOpen, handleChatToggle,
    isContactOpen, setIsContactOpen, handleContactToggle,
    currentView, handleNavigate,
    selectedStockSymbol, setSelectedStockSymbol,
    portfolioData
  } = useIndexData();

  const renderView = () => {
    switch (currentView) {
      case "dashboard":
        return (
          <DashboardView
            equity={portfolioData?.status === "success" ? portfolioData.equity : undefined}
            lastEquity={portfolioData?.status === "success" ? portfolioData.last_equity : undefined}
            positions={
              portfolioData?.status === "success" && portfolioData.positions
                ? portfolioData.positions.map((p) => ({
                    symbol: p.symbol,
                    qty: p.qty,
                    market_value: p.market_value,
                    unrealized_pnl: p.unrealized_pnl ?? 0,
                    unrealized_pnl_pct: p.unrealized_pnl_pct ?? 0,
                  }))
                : []
            }
            isConnected={portfolioData?.status === "success"}
          />
        );
      case "account":
        return <AccountView />;
      default:
        return null;
    }
  };

  // Home needs overflow-y:auto so the landing page can scroll
  const isHome = currentView === "home" && !selectedStockSymbol;

  return (
    <div
      className="min-h-screen bg-background text-foreground"
      style={{ overflowX: "hidden", overflowY: isHome ? "auto" : "hidden" }}
    >
      <div className="grain" />

      {/* Globe canvas only on home */}
      {isHome && <ChartBackground />}

      <Header
        currentView={currentView}
        onNavigate={handleNavigate}
        onChatClick={handleChatToggle}
      />

      <ChatPanel isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
      <ContactPanel isOpen={isContactOpen} onClose={() => setIsContactOpen(false)} />

      <AnimatePresence mode="wait">
        {selectedStockSymbol ? (
          <motion.main
            key="stock-detail"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="relative z-10"
          >
            <StockDetailView
              symbol={selectedStockSymbol}
              marketValue={
                portfolioData?.status === "success" && portfolioData.positions
                  ? portfolioData.positions.find((p) => p.symbol === selectedStockSymbol)?.market_value
                  : undefined
              }
              unrealizedPnlPct={
                portfolioData?.status === "success" && portfolioData.positions
                  ? portfolioData.positions.find((p) => p.symbol === selectedStockSymbol)?.unrealized_pnl_pct
                  : undefined
              }
              onBack={() => setSelectedStockSymbol(null)}
            />
          </motion.main>
        ) : currentView === "home" ? (
          <motion.main
            key="home"
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="relative z-10"
          >
            <LandingView />
          </motion.main>
        ) : (
          <motion.main
            key={currentView}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="relative z-10"
          >
            {renderView()}
          </motion.main>
        )}
      </AnimatePresence>
    </div>
  );
};

export default IndexV1;
