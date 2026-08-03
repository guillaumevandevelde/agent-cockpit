// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

// Regression guard for the jsdom `scrollIntoView` gap (kanban card
// c4565fbab6744398a8da02ca2e08b153). jsdom 29.1.1 does not implement
// `Element.prototype.scrollIntoView`, but Radix's Select calls
// `candidate?.scrollIntoView({ block: 'nearest' })` on the active option
// during its open transition (`@radix-ui/react-select/dist/index.mjs:348`).
// Without a stub the call throws a TypeError mid-open, which surfaces as a
// `waitFor` timeout and hides the assertion mismatch the test was actually
// written to catch.
describe("test-setup jsdom polyfills", () => {
  it("provides Element.prototype.scrollIntoView", () => {
    expect(typeof Element.prototype.scrollIntoView).toBe("function");
  });

  it("does not throw when Radix calls scrollIntoView with options", () => {
    const el = document.createElement("div");
    expect(() => el.scrollIntoView({ block: "nearest" })).not.toThrow();
  });
});
