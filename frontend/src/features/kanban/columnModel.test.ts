import { describe, expect, it } from "vitest";

import {
  hasClosedModelSet,
  modelForProviderChange,
  modelForProviderLoad,
} from "./columnModel";

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

// --- load path (the gap that let the "minimax shows no models" bug survive) --
//
// `modelForProviderChange` only fires from the provider Select's
// onValueChange — i.e. only when the user ACTIVELY switches provider. A
// column already persisted as (provider=minimax, model=opus) loads straight
// into the edit row with model="opus" and is never sanitised. That stale
// value then filters the datalist to zero matches ("no minimax models are
// shown") and makes Save fail with the backend's 422. These cover the load
// path so an invalid stored combo can't reach the form.

describe("hasClosedModelSet", () => {
  it("is true for providers the backend validates against a fixed list", () => {
    // Mirrors _allowed_models_for_provider in
    // backend/app/api/v1/kanban/router.py — these two return a list, so an
    // unknown model is a 422 and the UI must offer a closed picker.
    expect(hasClosedModelSet("minimax")).toBe(true);
    expect(hasClosedModelSet("anthropic")).toBe(true);
  });

  it("is false for bedrock", () => {
    // Bedrock has no model-options cache: AWS model ids are ARN-shaped
    // ("anthropic.claude-3-sonnet-20240229-v1:0"), never the bare aliases
    // the cli returns. The backend accepts any string, so the UI must keep
    // free-text entry — a closed dropdown would make bedrock unusable.
    expect(hasClosedModelSet("bedrock")).toBe(false);
  });

  it("is false when no provider is set", () => {
    // The "Default" sentinel: the dispatch chain picks the provider later,
    // so no closed set applies yet.
    expect(hasClosedModelSet(null)).toBe(false);
  });
});

describe("modelForProviderLoad", () => {
  it("clears a stored model that does not fit its own stored provider", () => {
    // THE BUG: this is the exact row found in the live DB —
    // ('engineer', 'minimax', 'opus'). It must not reach the form as "opus".
    expect(modelForProviderLoad("opus", "minimax", ["MiniMax-M3"])).toBe("");
  });

  it("keeps a stored model that fits its provider", () => {
    expect(modelForProviderLoad("MiniMax-M3", "minimax", ["MiniMax-M3"])).toBe(
      "MiniMax-M3",
    );
  });

  it("keeps a free-form model on a provider without a closed set", () => {
    // Bedrock ARNs are never in an options list but are perfectly valid;
    // clearing them here would silently wipe a working column's config.
    expect(
      modelForProviderLoad(
        "anthropic.claude-3-sonnet-20240229-v1:0",
        "bedrock",
        [],
      ),
    ).toBe("anthropic.claude-3-sonnet-20240229-v1:0");
  });

  it("keeps a stored model when the column pins no provider", () => {
    expect(modelForProviderLoad("opus", null, ["MiniMax-M3"])).toBe("opus");
  });

  it("maps a null stored model to the empty string", () => {
    // Columns store NULL for "unset"; the form field is a string.
    expect(modelForProviderLoad(null, "minimax", ["MiniMax-M3"])).toBe("");
  });

  it("clears rather than keeps when the options list has not loaded yet", () => {
    // Defensive: if the minimax fetch failed the list is the seed, not [].
    // But if it ever is empty, an unvalidatable model must not be presented
    // as valid — Save would 422 on it.
    expect(modelForProviderLoad("opus", "minimax", [])).toBe("");
  });
});
