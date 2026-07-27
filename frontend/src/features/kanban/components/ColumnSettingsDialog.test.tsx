// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getModelOptions, refreshModelOptions, updateColumn, getMinimaxModelOptions, refreshMinimaxModelOptions, getColumnEffectiveModel } = vi.hoisted(() => ({
  getModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku"] })),
  refreshModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku", "fable"] })),
  updateColumn: vi.fn(async (id: string, body: Record<string, unknown>) => ({
    id, project_key: "P", name: "engineer", rank: "0",
    default_agent: "engineer", default_provider: null, default_model: null,
    max_sessions: null, created_at: "", updated_at: "",
    ...body,
  })),
  getMinimaxModelOptions: vi.fn(async () => ({ provider: "minimax", options: ["MiniMax-M3", "MiniMax-M2.7"] })),
  refreshMinimaxModelOptions: vi.fn(async () => ({ provider: "minimax", options: ["MiniMax-M3", "MiniMax-M2.7"] })),
  getColumnEffectiveModel: vi.fn(async () => ({
    provider: "anthropic", model: "sonnet",
    provider_source: "column_default", model_source: "column_default",
    global_override: null, pool_choice: null,
    column_default_provider: "anthropic", column_default_model: "sonnet",
    persona_model: null,
  })),
}));

// The dialog reports failures through sonner; spy on it so the tests can
// assert WHICH message reaches the user (a generic "Failed to update
// column" hides the backend's 422 explanation — see the toast test below).
const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

vi.mock("../api", () => ({
  kanbanApi: {
    agents: vi.fn(async () => ({ agents: ["engineer", "analyst"] })),
    getModelOptions,
    refreshModelOptions,
    updateColumn,
    getMinimaxModelOptions,
    refreshMinimaxModelOptions,
    getColumnEffectiveModel,
  },
}));

import { ColumnSettingsDialog } from "./ColumnSettingsDialog";

const COLUMN = {
  id: "c1", project_key: "P", name: "engineer", rank: "0",
  default_agent: "engineer", default_provider: null, default_model: null,
  max_sessions: null, token_saver_enabled: false, created_at: "", updated_at: "",
};

/**
 * The model values on offer for the column currently in edit mode,
 * whichever control renders them: providers with a backend-enforced closed
 * set get a <select> (options are its children), the rest get an
 * <input list> + <datalist>. Reading both keeps these assertions about WHAT
 * is offered rather than which widget happens to offer it.
 *
 * The empty "Default" entry (= no column default) is filtered out so
 * toContain/not.toContain read as assertions about real models.
 */
function offeredModelValues(): string[] {
  const field = screen.getByLabelText(/default model/i);
  const source =
    field.tagName === "SELECT"
      ? field
      : document.getElementById(field.getAttribute("list")!)!;
  return Array.from(source.querySelectorAll("option"))
    .map((o) => (o as HTMLOptionElement).value)
    .filter((v) => v !== "");
}

afterEach(() => {
  cleanup();
  // vi.clearAllMocks() only clears the call history — mock implementations
  // (mockReturnValue / mockResolvedValue) survive across tests. Reset the
  // ones this file overrides per-test back to their vi.hoisted() default.
  getColumnEffectiveModel.mockReset();
  getColumnEffectiveModel.mockResolvedValue({
    provider: "anthropic", model: "sonnet",
    provider_source: "column_default", model_source: "column_default",
    global_override: null, pool_choice: null,
    column_default_provider: "anthropic", column_default_model: "sonnet",
    persona_model: null,
  });
  vi.clearAllMocks();
});

describe("ColumnSettingsDialog model field", () => {
  it("fetches model suggestions on open", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => expect(getModelOptions).toHaveBeenCalled());
  });

  it("submits the typed model as default_model on Save", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.change(screen.getByLabelText(/default model/i), { target: { value: "opus" } });
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(updateColumn).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ default_model: "opus" }),
    ));
  });

  it("refreshes the suggestion list when Refresh is clicked", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(refreshModelOptions).toHaveBeenCalled());
    // The minimax refresh is best-effort; the claude-code toast should be
    // the only user-visible signal.
    await waitFor(() => expect(refreshMinimaxModelOptions).toHaveBeenCalled());
  });

  // Regression for kanban card 1782fa43… (column model setting): the
  // datalist under the model input now uses the discovered JSONL list
  // (`getMinimaxModelOptions`) instead of the hardcoded
  // `["MiniMax-M3"]` constant. A subscription that has actually used
  // `MiniMax-M2.7` would not see it in the picker under the old
  // constant — kaart asked for the picker to reflect what the
  // subscription has actually run.
  it("uses the discovered minimax list when provider=minimax", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, default_provider: "minimax", default_model: "MiniMax-M3" }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    // First, the dynamic minimax options must be fetched on open.
    await waitFor(() => expect(getMinimaxModelOptions).toHaveBeenCalled());

    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    // The discovered list ("MiniMax-M3", "MiniMax-M2.7") wins over the
    // seed constant — so M2.7 must appear in the picker.
    expect(offeredModelValues()).toContain("MiniMax-M2.7");
  });

  // Kaart 1782fa43…: a board-wide subscription-override (or pool choice)
  // silently wins over a column's `default_provider` / `default_model`.
  // The dialog now surfaces that fact as an "Effective: …" line under
  // the column, so the user can see why their saved setting isn't
  // applied at dispatch time.
  it("renders an Effective line when an override is silently winning", async () => {
    // Override this one call to return a divergent effective-model.
    getColumnEffectiveModel.mockResolvedValue({
      provider: "minimax", model: "MiniMax-M3",
      provider_source: "global_override", model_source: "global_override",
      global_override: { provider: "minimax", model: "MiniMax-M3" },
      pool_choice: null,
      column_default_provider: "anthropic", column_default_model: "sonnet",
      persona_model: null,
    } as never);
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    // The EffectiveModelLine uses two sibling spans (one for the value,
    // one for "— from <source>"). Match the source label so the test
    // doesn't have to span JSX siblings.
    await waitFor(() =>
      expect(
        screen.getByText(/from board-wide subscription pin/i),
      ).toBeTruthy(),
    );
  });

  it("does NOT render an Effective line when only column defaults apply", async () => {
    // The default mock already returns provider_source=column_default.
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    // Wait for the effect to settle.
    await waitFor(() => expect(getColumnEffectiveModel).toHaveBeenCalled());
    // The "from board-wide subscription pin" phrase that only appears in
    // the EffectiveModelLine must NOT be present — the column's defaults
    // are already visible in the read-only row above.
    expect(
      screen.queryByText(/from board-wide subscription pin/i),
    ).toBeNull();
    expect(screen.queryByText(/from subscription pool/i)).toBeNull();
  });

  // Regression for kanban card "Column model setting": the minimax datalist
  // historically suggested the bare `MiniMax-M3` AND the bracketed
  // `MiniMax-M3[1m]` context-window suffix form. The backend dropped the
  // `[1m]` form on 2026-07-17 (commit 0ce81be — MiniMax's API rejects it as
  // an unknown model), but the frontend constant was never updated. A user
  // picking the invalid form from the datalist gets their saved column
  // default rejected by the API, the spawn silently falls back to
  // anthropic/opus — the column "stays stuck on opus" exactly as reported.
  //
  // The provider dropdown drives `modelSuggestionsForProvider` via the
  // dialog's editProvider state; switching it to "minimax" must swap the
  // datalist to a list of ONLY bare model identifiers the backend accepts.
  // Pre-fix the list still contained `MiniMax-M3[1m]`; the failing assertion
  // is `not.toContain("MiniMax-M3[1m]")`.
  it("swaps the model datalist to bare minimax models when provider=minimax", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // We assert on the options actually offered, since that's what the user
    // can reach. With no provider pinned the field is still free-text (no
    // closed set applies) and falls back to the loaded claude-code
    // model-options — sonnet/opus/haiku.
    const beforeValues = offeredModelValues();
    expect(beforeValues).toContain("opus");
    expect(beforeValues).not.toContain("MiniMax-M3");

    // Switch the provider dropdown to MiniMax. Radix Select needs a real
    // pointer event sequence for the dropdown to open, which is fragile in
    // jsdom, so we re-render with a fresh column already on provider=minimax
    // and assert the offered options there instead.
    cleanup();
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, default_provider: "minimax", default_model: "MiniMax-M3" }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const minimaxValues = offeredModelValues();
    expect(minimaxValues).toContain("MiniMax-M3");
    // The bracketed `[1m]` form is invalid on MiniMax's API and must never
    // be offered as a picker suggestion — this is the regression guard.
    expect(minimaxValues).not.toContain("MiniMax-M3[1m]");
  });
});

describe("ColumnSettingsDialog session limit (pause)", () => {
  it("renders null max_sessions as ∞", () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, max_sessions: null }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("∞")).toBeTruthy();
    expect(screen.queryByText("Paused")).toBeNull();
  });

  it("renders max_sessions=0 as Paused", () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, max_sessions: 0 }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("Paused")).toBeTruthy();
  });

  it("renders a positive max_sessions as max n", () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, max_sessions: 3 }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    expect(screen.getByText("max 3")).toBeTruthy();
  });

  it("PATCHes max_sessions=null when the ∞ button is clicked", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, max_sessions: 2 }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: "∞" }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(updateColumn).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ max_sessions: null }),
    ));
  });

  it("PATCHes max_sessions=0 when the Pause button is clicked", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{ ...COLUMN, max_sessions: 2 }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /^pause$/i }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(updateColumn).toHaveBeenCalledWith(
      "c1",
      expect.objectContaining({ max_sessions: 0 }),
    ));
  });
});

// --- edit row layout -----------------------------------------------------
//
// Regression guard for kanban card a6316dba…: the edit row packs agent +
// provider + model + max-sessions + token-saver + Save/Cancel. The row's
// natural width (~1220px) exceeds LG's 828px usable width, so without a
// graceful overflow strategy the row was squished into oblivion (agent
// dropdown rendered at 60px of its 192px request, provider at 109px of
// 160px, "Token saver (RTK) — reduces Bash output" broke over 3 lines).
//
// The first attempt at this card moved MD → LG (128px wider dialog) and
// shipped a regression test that pinned only the modal-width *constant*
// (`max-w-4xl` present, `max-w-3xl` absent). That guard passed while the
// bug was still there — the const-revert path was the only failure mode
// it caught. The product-effect fix is `flex-wrap` + `shrink-0` on every
// fixed-width control so the row wraps to a second line instead of being
// crushed. This test pins that design, not the width constant, so a
// future revert that removes wrap+shrink-0 still fails the build.
describe("ColumnSettingsDialog edit row layout", () => {
  it("wraps controls and prevents squishing on narrow widths", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // The row container must wrap — without flex-wrap the controls stay
    // on one line and get crushed down to min-content. data-testid hooks
    // ride alongside the visual fix so this guard survives a future
    // className rename that keeps the look broken.
    const row = screen.getByTestId("column-row");
    expect(row.className).toContain("flex-wrap");
    expect(row.className).toContain("items-center");

    // Each fixed-width control must opt out of shrinking. A bug that
    // drops shrink-0 from any of these puts that control back into the
    // crushed state (60px agent / 109px provider / 3-line label).
    const agentTrigger = screen.getByTestId("column-row-agent-trigger");
    expect(agentTrigger.className).toContain("shrink-0");
    expect(agentTrigger.className).toContain("w-48");

    const providerTrigger = screen.getByTestId("column-row-provider-trigger");
    expect(providerTrigger.className).toContain("shrink-0");
    expect(providerTrigger.className).toContain("w-40");

    // The token-saver label must keep its content on one logical piece
    // — without shrink-0 the parent flex crushed the "—" hint and
    // "Bash output" broke mid-word across three lines.
    const tokenSaverLabel = screen.getByTestId("column-row-token-saver");
    expect(tokenSaverLabel.className).toContain("shrink-0");
    expect(tokenSaverLabel.className).toContain("whitespace-nowrap");
  });
});

// --- stale (provider, model) combos already in the database ----------------
//
// The co-validation fix guarded new WRITES but never migrated rows written
// before it landed. The live DB still held ('engineer', 'minimax', 'opus'),
// which reproduced the original report in full: the model field loaded as
// "opus", the datalist filtered its MiniMax options down to zero matches,
// and Save came back 422 — the column could not be repaired through the UI.
//
// Note the pre-existing suite only ever exercised (minimax, MiniMax-M3), a
// VALID pair, which is exactly why it stayed green through the bug.

const MINIMAX_STALE = {
  ...COLUMN,
  default_provider: "minimax",
  default_model: "opus",
};

describe("ColumnSettingsDialog with a stale provider/model combo", () => {
  it("does not load an invalid stored model into the model field", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[MINIMAX_STALE]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    // Wait for the minimax option list to arrive — the sanitising decision
    // needs it, so asserting before it lands would test the wrong state.
    await waitFor(() => expect(getMinimaxModelOptions).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // "opus" is not a MiniMax model; it must not be presented as the
    // column's current value. Empty means "let the dispatch chain choose",
    // which for minimax resolves to MINIMAX_DEFAULT_MODEL.
    await waitFor(() => {
      const field = screen.getByLabelText(/default model/i) as HTMLInputElement;
      expect(field.value).not.toBe("opus");
    });
  });

  it("offers the minimax models as selectable options, not filtered-away text", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[MINIMAX_STALE]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => expect(getMinimaxModelOptions).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));

    // The heart of the report: the MiniMax models must be reachable. A
    // native <select> keeps every option in the DOM regardless of the
    // current value, so no stale value can filter them out of existence.
    await waitFor(() => {
      const values = Array.from(
        document.querySelectorAll("#default-model-c1 option"),
      ).map((o) => (o as HTMLOptionElement).value);
      expect(values).toContain("MiniMax-M3");
      expect(values).toContain("MiniMax-M2.7");
      expect(values).not.toContain("opus");
    });
  });

  it("keeps free-text entry for bedrock, which has no closed model set", async () => {
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[{
          ...COLUMN,
          default_provider: "bedrock",
          default_model: "anthropic.claude-3-sonnet-20240229-v1:0",
        }]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    // An ARN-shaped id is in no options list but is perfectly valid; a
    // dropdown here would make bedrock columns unconfigurable.
    const field = screen.getByLabelText(/default model/i) as HTMLInputElement;
    expect(field.tagName).toBe("INPUT");
    expect(field.value).toBe("anthropic.claude-3-sonnet-20240229-v1:0");
  });

  it("surfaces the backend's rejection reason instead of a generic failure", async () => {
    // The bare `catch { toast.error("Failed to update column") }` threw away
    // the one message that explained the whole bug. apiClient already
    // rethrows the 422 detail as error.message — just don't discard it.
    updateColumn.mockRejectedValueOnce(
      new Error("model 'opus' is not valid for provider 'minimax'; known options: ['MiniMax-M3']"),
    );
    render(
      <ColumnSettingsDialog
        open
        projectKey="P"
        projectPath="/p"
        columns={[COLUMN]}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    fireEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        expect.stringContaining("not valid for provider 'minimax'"),
      ),
    );
  });
});
