// Vitest setup: registers custom jest-dom matchers (`toBeInTheDocument`,
// `toHaveTextContent`, etc.) so they extend Vitest's `expect` globally.
// Wired via `test.setupFiles` in `vite.config.ts`. See kanban card
// 84b7bc21981f40a6ab31d5a118a733aa for context.

// jsdom 29.1.1 does not implement `Element.prototype.scrollIntoView`, but
// Radix's Select calls `candidate?.scrollIntoView({ block: 'nearest' })` on
// the active option during its open transition
// (`@radix-ui/react-select/dist/index.mjs:348`). Without this no-op stub the
// call throws mid-open, which surfaces as a `waitFor` timeout and masks the
// assertion mismatch the test was actually written to catch. What makes the
// stub early enough is that this whole setup file runs before any test file
// renders — not its position relative to the import below, which ES module
// hoisting evaluates first regardless of source order. The `typeof Element`
// guard is load-bearing: setup files also run for tests without a
// `@vitest-environment jsdom` docblock, where `Element` is undefined.
// Kanban card c4565fbab6744398a8da02ca2e08b153.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function () {};
}

import "@testing-library/jest-dom/vitest";
