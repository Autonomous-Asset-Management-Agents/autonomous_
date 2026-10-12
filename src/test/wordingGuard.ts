// Shared wording guard for public surfaces (#4461, #4516; Epic #4450).
// Same rule as the #4457 wording guard (websiteCopy.test.ts).
export const FORBIDDEN = /asset[ -]management|profitab\w*|\bsafe\b|\bsicher\b|rendite\w*/gi;

// Link targets are identifiers, not copy: the GitHub organisation is named
// "Autonomous-Asset-Management-Agents", so every repo URL would otherwise count as a hit.
export const withoutLinkTargets = (s: string) => s.replace(/\]\([^)]*\)/g, "]");
