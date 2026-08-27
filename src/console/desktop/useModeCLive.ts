import { useEffect, useState } from "react";
import { getHitlPolicy } from "@/lib/api";
import { useStore } from "@/console/store/useStore";

/**
 * H2 (#2370): live-polls the engine's HITL policy; true only when the engine is LIVE
 * (paper_trading === false) AND Mode C (HITL_AUTONOMOUS_UNLIMITED) is on. Drives the
 * persistent ModeCLiveBanner disclosure.
 */
export function useModeCLive(): boolean {
  const paperTrading = useStore((s) => s.paperTrading);
  const [unlimited, setUnlimited] = useState(false);
  useEffect(() => {
    let alive = true;
    const poll = () => {
      if (typeof getHitlPolicy !== "function") return Promise.resolve();
      return getHitlPolicy()
        .then((p) => {
          if (alive && p) setUnlimited(p.HITL_AUTONOMOUS_UNLIMITED === true);
        })
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);
  return paperTrading === false && unlimited;
}
