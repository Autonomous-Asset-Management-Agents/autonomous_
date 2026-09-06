import type { ButtonHTMLAttributes, ReactNode } from "react";

/**
 * Standard CTA pill-button used across the console — sidebar, settings, cards.
 * Two tones: "green" (default, Start Engine / Upgrade) and "red" (Kill Switch).
 *
 * Width defaults to the sidebar button width (178px); pass `fullWidth` for
 * sidebar-style `w-full`, or override with className.
 */

const TONE = {
  green: "bg-[#00c27a] hover:bg-[#00d687]",
  red: "bg-[#ff5a52] hover:bg-[#ff6c65]",
} as const;

type CtaButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tone?: keyof typeof TONE;
  fullWidth?: boolean;
  children: ReactNode;
};

export function CtaButton({
  tone = "green",
  fullWidth = false,
  className = "",
  children,
  ...rest
}: CtaButtonProps) {
  return (
    <button
      className={[
        fullWidth ? "w-full" : "w-[178px]",
        "rounded-full px-6 py-2.5 text-[13px] font-bold tracking-wide uppercase",
        "text-white border-none transition-all transform active:scale-[0.97]",
        "disabled:cursor-not-allowed disabled:opacity-60",
        "flex items-center justify-center gap-2",
        TONE[tone],
        className,
      ].join(" ")}
      {...rest}
    >
      {children}
    </button>
  );
}
