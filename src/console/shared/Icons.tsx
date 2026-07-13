import type { SVGProps } from "react";

// Console icon set — vendored from the desktop bundle (G3, #1050). Stroke-based,
// currentColor, 24-grid. Self-contained (no deps beyond React's SVGProps).

const I = (p: SVGProps<SVGSVGElement>) => (
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" {...p} />
);

export const IconDashboard = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M3 13h7V3H3zM14 21h7V11h-7zM14 3v6h7V3zM3 21h7v-6H3z" /></I>
);
export const IconQueue = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M4 6h16M4 12h16M4 18h10" /></I>
);
export const IconPositions = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M3 3v18h18" /><path d="M7 14l3-3 4 4 5-6" /></I>
);
export const IconActivity = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M22 12h-4l-3 9L9 3l-3 9H2" /></I>
);
export const IconReports = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" /><path d="M14 2v6h6M8 13h8M8 17h5" /></I>
);
export const IconAudit = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></I>
);
export const IconSettings = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></I>
);
export const IconCheck = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M20 6 9 17l-5-5" /></I>
);
export const IconFingerprint = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}>
    <path d="M12 11a4 4 0 0 0-4 4v2" />
    <path d="M16 11.5V13a8 8 0 0 1-2.5 5.8" />
    <path d="M9 21.5A14 14 0 0 0 12 12a5 5 0 0 1 10 0v3" />
    <path d="M2 12a10 10 0 0 1 18-6.5" />
    <path d="M3 18a14 14 0 0 1 2-2" />
  </I>
);
export const IconChat = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M21 11.5a8.38 8.38 0 0 1-8.5 8.5 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7A8.5 8.5 0 0 1 12.5 3 8.38 8.38 0 0 1 21 11.5z" /></I>
);
export const IconX = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M18 6 6 18M6 6l12 12" /></I>
);
export const IconArrowUp = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M12 19V5M5 12l7-7 7 7" /></I>
);
export const IconArrowDown = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M12 5v14M19 12l-7 7-7-7" /></I>
);
export const IconShield = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /></I>
);
export const IconChevronRight = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="m9 18 6-6-6-6" /></I>
);
export const IconChevronLeft = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="m15 18-6-6 6-6" /></I>
);
export const IconHome = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><path d="M9 22V12h6v10" /></I>
);
export const IconBolt = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M13 2 3 14h7l-1 8 10-12h-7z" /></I>
);
export const IconBrandWindows = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="m3 5 8-1v7H3zm10-1.15 8-1.15V11h-8zM3 13h8v7l-8-1zm10 0h8v8.3l-8-1.15z" /></I>
);
export const IconBrandGithub = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}><path d="M9 19c-4.3 1.4-4.3-2.5-6-3m12 5v-3.5c0-1 .1-1.4-.5-2 2.8-.3 5.5-1.4 5.5-6a4.6 4.6 0 0 0-1.3-3.2 4.2 4.2 0 0 0-.1-3.2s-1.1-.3-3.5 1.3a12.3 12.3 0 0 0-6.2 0C4.8 2.5 3.7 2.8 3.7 2.8a4.2 4.2 0 0 0-.1 3.2A4.6 4.6 0 0 0 2.3 9.2c0 4.6 2.7 5.7 5.5 6-.6.6-.6 1.2-.5 2V21" /></I>
);
export const IconLightbulb = (p: SVGProps<SVGSVGElement>) => (
  <I {...p}>
    <path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A5.5 5.5 0 0 0 12.5 2.5a5.5 5.5 0 0 0-5.5 5.5c0 1.3.5 2.6 1.5 3.5.8.8 1.3 1.5 1.5 2.5" />
    <path d="M9 18h6" />
    <path d="M10 22h4" />
  </I>
);
