import React from "react";
import { cn } from "@/lib/utils";

export interface SidebarNavProps extends React.HTMLAttributes<HTMLElement> {
  items: {
    href: string; // Used as the tab identifier
    title: string;
    icon?: React.ReactNode;
  }[];
  activeHref: string;
  onTabChange: (href: string) => void;
}

export function SidebarNav({ className, items, activeHref, onTabChange, ...props }: SidebarNavProps) {
  return (
    <nav className={cn("flex space-x-2 lg:flex-col lg:space-x-0 lg:space-y-1", className)} {...props}>
      {items.map((item) => {
        const isActive = activeHref === item.href;
        return (
          <button
            key={item.href}
            onClick={() => onTabChange(item.href)}
            className={cn(
              "flex items-center gap-3 px-4 py-2.5 rounded-xl text-[14px] font-medium transition-all duration-200",
              isActive
                ? "bg-white/10 text-white shadow-sm"
                : "text-white/50 hover:bg-white/5 hover:text-white/80"
            )}
          >
            {item.icon && <span className={cn("shrink-0", isActive ? "text-white" : "text-white/40")}>{item.icon}</span>}
            {item.title}
          </button>
        );
      })}
    </nav>
  );
}
