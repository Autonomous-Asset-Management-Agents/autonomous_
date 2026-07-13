import type { CSSProperties } from "react";
import { useStore } from "@/console/store/useStore";
import { isDesktop, minimizeWindow, toggleMaximizeWindow, closeWindow } from "@/lib/desktopBridge";

// The desktop window has no native frame (Electron `frame:false`); this bar IS
// the title bar — draggable, logo on the left, window controls on the right.
// All Electron IPC goes through desktopBridge — no direct window.aaagents here.
// In the cloud build the bridge is absent, so the drag region + controls don't apply.

// `-webkit-app-region` isn't in React's CSS types — cast through a helper.
const dragStyle = { WebkitAppRegion: "drag" } as CSSProperties;
const noDragStyle = { WebkitAppRegion: "no-drag" } as CSSProperties;

function WindowControls() {
  const btn =
    "w-11 h-9 flex items-center justify-center text-white/50 hover:text-white hover:bg-white/10 transition-colors";
  return (
    <div className="flex items-center" style={noDragStyle}>
      <button className={btn} title="Minimize" onClick={() => minimizeWindow()}>
        <svg width="10" height="10" viewBox="0 0 10 10"><rect x="0" y="4.5" width="10" height="1" fill="currentColor" /></svg>
      </button>
      <button className={btn} title="Maximize" onClick={() => toggleMaximizeWindow()}>
        <svg width="10" height="10" viewBox="0 0 10 10"><rect x="0.5" y="0.5" width="9" height="9" fill="none" stroke="currentColor" /></svg>
      </button>
      <button
        className="w-11 h-9 flex items-center justify-center text-white/50 hover:text-white hover:bg-[#e81123] transition-colors"
        title="Close"
        onClick={() => closeWindow()}
      >
        <svg width="10" height="10" viewBox="0 0 10 10"><path d="M0 0 L10 10 M10 0 L0 10" stroke="currentColor" strokeWidth="1" /></svg>
      </button>
    </div>
  );
}

export function TitleBar() {
  const brokerName = useStore((s) => s.brokerName);
  const accountTag = useStore((s) => s.accountTag);
  const desktop = isDesktop();

  return (
    <div
      className="h-9 flex items-center bg-black/60 border-b border-white/5 backdrop-blur-md select-none"
      style={desktop ? dragStyle : undefined}
    >
      <div className="flex items-center gap-2.5 pl-3.5 pr-4">
        <img src="/favicon.svg" alt="autonomous_" className="w-[22px] h-[22px] rounded-[5px]" draggable={false} />
        <span className="text-[14.5px] font-bold tracking-tight text-white/92 leading-none">
          autonomous<span style={{ color: "#00c27a" }}>_</span>
        </span>
      </div>

      {/* Center: account / broker — only once the engine reports a real connected broker. */}
      <div className="flex-1 flex items-center justify-center gap-2 text-[11px] text-white/30">
        {brokerName && accountTag ? (
          <>
            <span className="num">{accountTag}</span>
            <span className="text-white/16">·</span>
            <span>{brokerName}</span>
          </>
        ) : null}
      </div>

      {/* Right: window controls (desktop only). */}
      <div className="flex items-center gap-3">
        {desktop ? <WindowControls /> : <div className="pr-3" />}
      </div>
    </div>
  );
}
