import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import {
  Home,
  LayoutDashboard,
  TrendingUp,
  PlayCircle,
  Briefcase,
  GraduationCap,
  FileText,
  Power
} from "lucide-react";
import { isPublicViewOnly } from "@/lib/publicMode";

interface MenuItemData {
  id: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  description: string;
  /** Hidden in public view-only mode (e.g. aaagents.de) */
  controlOnly?: boolean;
}

const menuItems: MenuItemData[] = [
  { id: "home", label: "Home", icon: Home, description: "Portfolio overview" },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, description: "Account & AI insights" },
  { id: "strategy", label: "Strategy", icon: TrendingUp, description: "Control trading", controlOnly: true },
  { id: "simulation", label: "Simulation", icon: PlayCircle, description: "Backtest strategies", controlOnly: true },
  { id: "portfolio", label: "Portfolio", icon: Briefcase, description: "Holdings & positions", controlOnly: true },
  { id: "learning", label: "Learning", icon: GraduationCap, description: "Train the model", controlOnly: true },
  { id: "logs", label: "Logs", icon: FileText, description: "System & news", controlOnly: true },
  { id: "engine", label: "Engine Status", icon: Power, description: "Connection status", controlOnly: true },
];

interface FullscreenMenuProps {
  isOpen: boolean;
  currentView: string;
  onNavigate: (viewId: string) => void;
}

export const FullscreenMenu = ({ isOpen, currentView, onNavigate }: FullscreenMenuProps) => {
  if (!isOpen) return null;

  const menuContent = (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="fixed inset-0 z-40 bg-background/98 backdrop-blur-sm flex items-center justify-center"
    >
      <div className="flex flex-col items-start px-8 sm:px-12">
        <nav>
          <ul className="space-y-2 sm:space-y-3">
            {menuItems
              .filter((item) => !isPublicViewOnly() || !item.controlOnly)
              .map((item, index) => {
              const Icon = item.icon;
              const isActive = currentView === item.id;

              return (
                <motion.li
                  key={item.id}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{
                    duration: 0.4,
                    delay: index * 0.05,
                    ease: [0.16, 1, 0.3, 1]
                  }}
                >
                  <button
                    onClick={() => onNavigate(item.id)}
                    className={`group flex items-center gap-3 sm:gap-4 py-1.5 sm:py-2 transition-all duration-300 ${
                      isActive
                        ? 'text-foreground'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Icon className="w-5 h-5 sm:w-6 sm:h-6 flex-shrink-0 opacity-60 group-hover:opacity-100 transition-opacity" />
                    <span className="font-display text-lg sm:text-xl md:text-2xl">
                      {item.label}
                    </span>
                  </button>
                </motion.li>
              );
            })}
          </ul>
        </nav>

        {/* Footer info */}
        <motion.div
          className="mt-8 sm:mt-10 text-[10px] sm:text-xs text-muted-foreground"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.5 }}
        >
          <p>ai_trading_bot • Autonomous Portfolio Management</p>
        </motion.div>
      </div>
    </motion.div>
  );

  return createPortal(menuContent, document.body);
};
