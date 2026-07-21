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
  max_sessions: null, created_at: "", updated_at: "",
};

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
    const modelInput = screen.getByLabelText(/default model/i) as HTMLInputElement;
    const listId = modelInput.getAttribute("list")!;
    const datalist = document.getElementById(listId)!;
    const values = Array.from(datalist.querySelectorAll("option")).map(
      (o) => (o as HTMLOptionElement).value,
    );
    // The discovered list ("MiniMax-M3", "MiniMax-M2.7") wins over the
    // seed constant — so M2.7 must appear in the picker.
    expect(values).toContain("MiniMax-M2.7");
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

    // shadcn Select renders options into a portal; pull trigger text + open
    // the provider dropdown via clicking its trigger. We assert on the
    // rendered datalist directly since that's what the user actually sees
    // when typing into the model input.
    // The dialog renders one datalist per column keyed by column id, so the
    // engineer column's `default-model-${id}` input has its own datalist.
    const modelInput = screen.getByLabelText(/default model/i) as HTMLInputElement;
    const listId = modelInput.getAttribute("list")!;
    const beforeValues = Array.from(
      document.getElementById(listId)!.querySelectorAll("option"),
    ).map((o) => (o as HTMLOptionElement).value);
    // Pre-condition: with the default (Anthropic) provider selected the
    // datalist falls back to the loaded model-options — sonnet/opus/haiku.
    expect(beforeValues).toContain("opus");
    expect(beforeValues).not.toContain("MiniMax-M3");

    // Switch the provider dropdown to MiniMax. Radix Select needs a real
    // pointer event sequence for the dropdown to open; a direct value change
    // via fireEvent re-uses the existing select and just swaps its value.
    // We re-render with a fresh column that already has provider=minimax to
    // assert the same datalist cleanly, since opening the Radix dropdown in
    // jsdom is fragile.
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
    const minimaxInput = screen.getByLabelText(/default model/i) as HTMLInputElement;
    const minimaxListId = minimaxInput.getAttribute("list")!;
    const minimaxValues = Array.from(
      document.getElementById(minimaxListId)!.querySelectorAll("option"),
    ).map((o) => (o as HTMLOptionElement).value);
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
