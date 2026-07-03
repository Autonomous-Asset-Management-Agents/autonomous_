import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchPortfolioSummary, fetchStrategy } from "@/lib/api";
import { useAuthState } from "@/components/useAuthState";
import { useNavigate } from "react-router-dom";

export type ViewType = "home" | "dashboard" | "account";

export const useIndexData = () => {
  const navigate = useNavigate();
  const { user } = useAuthState();
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isContactOpen, setIsContactOpen] = useState(false);
  const [currentView, setCurrentView] = useState<ViewType>("home");
  const [selectedStockSymbol, setSelectedStockSymbol] = useState<string | null>(null);

  const { data: strategyData } = useQuery({
    queryKey: ["strategy"],
    queryFn: fetchStrategy,
    refetchInterval: 5000,
    retry: true,
    enabled: !!user,
  });

  const { data: portfolioData } = useQuery({
    queryKey: ["portfolio-summary"],
    queryFn: fetchPortfolioSummary,
    refetchInterval: 10000,
    retry: true,
    enabled: !!user,
  });

  const handleNavigate = (viewId: string) => {
    // Dashboard and Account require login
    if ((viewId === "dashboard" || viewId === "account") && !user) {
      navigate("/login");
      return;
    }
    setSelectedStockSymbol(null);
    setCurrentView(viewId as ViewType);
    // Scroll to top when switching away from home
    if (viewId !== "home") window.scrollTo({ top: 0 });
  };

  const handleChatToggle = () => setIsChatOpen(!isChatOpen);
  const handleContactToggle = () => {
    setIsContactOpen(!isContactOpen);
    if (!isContactOpen) setIsChatOpen(false);
  };

  return {
    user,
    isChatOpen, setIsChatOpen, handleChatToggle,
    isContactOpen, setIsContactOpen, handleContactToggle,
    currentView, setCurrentView, handleNavigate,
    selectedStockSymbol, setSelectedStockSymbol,
    strategyData,
    portfolioData
  };
};
