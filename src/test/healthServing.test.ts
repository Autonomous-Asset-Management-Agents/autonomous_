import { describe, it, expect } from "vitest";
import { nextServingState } from "@/console/live/healthServing";

describe("nextServingState — debounced engine 'serving' signal", () => {
  it("a reachable healthy poll → serving true, streak reset", () => {
    expect(nextServingState({ current: false, reachable: true, starting: false, failStreak: 2 }))
      .toEqual({ serving: true, failStreak: 0 });
  });

  it("an AFFIRMATIVE status:'starting' → not serving immediately (genuine boot, not a stall)", () => {
    expect(nextServingState({ current: true, reachable: true, starting: true, failStreak: 0 }))
      .toEqual({ serving: false, failStreak: 0 });
  });

  it("a single unreachable poll does NOT flip a serving engine to false (transient stall)", () => {
    // the engine's event loop stalled for ~16s → one /health failed → keep 'serving' true
    expect(nextServingState({ current: true, reachable: false, starting: false, failStreak: 0 }))
      .toEqual({ serving: true, failStreak: 1 });
    expect(nextServingState({ current: true, reachable: false, starting: false, failStreak: 1 }))
      .toEqual({ serving: true, failStreak: 2 });
  });

  it("only after `threshold` consecutive failures → not serving (genuine outage)", () => {
    expect(nextServingState({ current: true, reachable: false, starting: false, failStreak: 2 }))
      .toEqual({ serving: false, failStreak: 3 });
  });

  it("recovery: a reachable poll after failures → serving true, streak reset", () => {
    expect(nextServingState({ current: false, reachable: true, starting: false, failStreak: 5 }))
      .toEqual({ serving: true, failStreak: 0 });
  });

  it("custom threshold is honored", () => {
    // threshold 1 = flip on the first failure (old eager behaviour)
    expect(nextServingState({ current: true, reachable: false, starting: false, failStreak: 0 }, 1))
      .toEqual({ serving: false, failStreak: 1 });
  });

  it("during boot (current=false) an unreachable poll stays not-serving (no false 'up')", () => {
    expect(nextServingState({ current: false, reachable: false, starting: false, failStreak: 0 }))
      .toEqual({ serving: false, failStreak: 1 });
  });
});
