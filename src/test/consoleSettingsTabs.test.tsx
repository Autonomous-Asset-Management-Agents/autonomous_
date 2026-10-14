import React, { useState } from "react";
import { render, fireEvent, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { SidebarNav } from "@/console/desktop/SidebarNav";

describe("SidebarNav", () => {
  const items = [
    { href: "trading", title: "Trading" },
    { href: "system", title: "System" },
    { href: "notifications", title: "Notifications" },
  ];

  it("renders all items", () => {
    render(<SidebarNav items={items} activeHref="trading" onTabChange={() => {}} />);
    expect(screen.getByText("Trading")).toBeTruthy();
    expect(screen.getByText("System")).toBeTruthy();
    expect(screen.getByText("Notifications")).toBeTruthy();
  });

  it("calls onTabChange when a tab is clicked", () => {
    const onTabChange = vi.fn();
    render(<SidebarNav items={items} activeHref="trading" onTabChange={onTabChange} />);
    
    fireEvent.click(screen.getByText("System"));
    expect(onTabChange).toHaveBeenCalledWith("system");
  });

  it("applies the active styling to the correct tab", () => {
    // We can test this by checking class names. Active tab has 'bg-white/10'
    render(<SidebarNav items={items} activeHref="system" onTabChange={() => {}} />);
    
    const tradingTab = screen.getByText("Trading");
    const systemTab = screen.getByText("System");
    
    expect(tradingTab.className).toContain("text-white/50");
    expect(systemTab.className).toContain("bg-white/10");
  });
});
