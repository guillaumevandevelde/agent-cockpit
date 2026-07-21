import { describe, expect, it } from "vitest";

import { modelForProviderChange } from "./columnModel";

const ANTHROPIC_OPTIONS = ["sonnet", "opus", "haiku"];
const MINIMAX_OPTIONS = ["MiniMax-M3", "MiniMax-M2.7"];

describe("modelForProviderChange", () => {
  it("clears the model when switching from anthropic to minimax with opus", () => {
    // The bug: a column at provider=anthropic with model=opus was
    // switched to provider=minimax, model stayed opus, the column
    // "stuck on opus" because the API accepted the mismatch.
    expect(
      modelForProviderChange("opus", "anthropic", "minimax", MINIMAX_OPTIONS),
    ).toBe("");
  });

  it("keeps the model when switching to a provider whose options contain it", () => {
    // Some MiniMax models share aliases with claude-code in older usage;
    // if the user happens to be on one, don't wipe their input.
    expect(
      modelForProviderChange(
        "MiniMax-M3",
        "anthropic",
        "minimax",
        MINIMAX_OPTIONS,
      ),
    ).toBe("MiniMax-M3");
  });

  it("keeps the model when the provider hasn't changed", () => {
    // Picking the same provider twice in a row (rare but possible) is a
    // no-op for the model field.
    expect(
      modelForProviderChange("opus", "anthropic", "anthropic", ANTHROPIC_OPTIONS),
    ).toBe("opus");
  });

  it("keeps the model when the new provider is unset (null)", () => {
    // Switching to "no provider" (the Default sentinel) drops the
    // validation constraint — the dispatch chain will pick the provider
    // later, and the model is still a valid choice for whatever it
    // resolves to.
    expect(
      modelForProviderChange("opus", "minimax", null, ANTHROPIC_OPTIONS),
    ).toBe("opus");
  });

  it("leaves an empty model empty", () => {
    // The user cleared the field already; nothing to do.
    expect(
      modelForProviderChange("", "anthropic", "minimax", MINIMAX_OPTIONS),
    ).toBe("");
  });

  it("clears the model when switching from minimax to anthropic with a MiniMax model", () => {
    // Symmetric: a MiniMax-only model doesn't fit anthropic.
    expect(
      modelForProviderChange(
        "MiniMax-M3",
        "minimax",
        "anthropic",
        ANTHROPIC_OPTIONS,
      ),
    ).toBe("");
  });

  it("treats a missing old provider as 'any'", () => {
    // The column had no provider set; the user picks minimax for the
    // first time. opus doesn't fit minimax → clear.
    expect(
      modelForProviderChange("opus", null, "minimax", MINIMAX_OPTIONS),
    ).toBe("");
  });
});
