import type { ConsolePage } from "@/console/store/useStore";
import {
  IconDashboard,
  IconQueue,
  IconPositions,
  IconActivity,
  IconReports,
  IconAudit,
  IconSettings,
  IconBolt,
} from "@/console/shared/Icons";

/**
 * Console nav registry + entitlement gating (extracted from Sidebar/DesktopApp so it
 * is a pure, cheap-to-test unit — no component/hook graph).
 *
 * The Simulation/backtest page is gated: it appears in the sidebar and is a valid
 * ?page= target ONLY when the engine's resolved entitlement enables it
 * (`simulationEnabled === true`). It is hidden by default (null/false) while the
 * backtest runtime is hardened. Central switch: core/entitlement/tier.py
 * (`simulation_enabled`, currently False for every tier).
 */
export type NavItem = { id: ConsolePage; label: string; Icon: typeof IconDashboard };

const ALL_ITEMS: NavItem[] = [
  { id: "overview", label: "Overview", Icon: IconDashboard },
  { id: "decisions", label: "Decisions", Icon: IconQueue },
  { id: "positions", label: "Positions", Icon: IconPositions },
  { id: "activities", label: "Activities", Icon: IconActivity },
  { id: "reports", label: "Reports", Icon: IconReports },
  { id: "simulation", label: "Simulation", Icon: IconBolt },
  { id: "audit", label: "Audit chain", Icon: IconAudit },
  { id: "settings", label: "Settings", Icon: IconSettings },
];

/** Sidebar nav items for the current entitlement (Simulation hidden unless enabled). */
export function navItems(simulationEnabled: boolean): NavItem[] {
  return ALL_ITEMS.filter((i) => i.id !== "simulation" || simulationEnabled);
}

/** Valid ?page= direct-nav targets (Simulation deep-link ignored unless enabled). */
export function validPages(simulationEnabled: boolean): Set<ConsolePage> {
  const pages: ConsolePage[] = [
    "overview",
    "decisions",
    "positions",
    "activities",
    "reports",
    "audit",
    "settings",
    "chat",
  ];
  if (simulationEnabled) pages.push("simulation");
  return new Set(pages);
}
