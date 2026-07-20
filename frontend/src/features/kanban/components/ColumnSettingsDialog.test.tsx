// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

const { getModelOptions, refreshModelOptions, updateColumn } = vi.hoisted(() => ({
  getModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku"] })),
  refreshModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku", "fable"] })),
  updateColumn: vi.fn(async (id: string, body: Record<string, unknown>) => ({
    id, project_key: "P", name: "engineer", rank: "0",
    default_agent: "engineer", default_provider: null, default_model: null,
    max_sessions: null, created_at: "", updated_at: "",
    ...body,
  })),
}));

vi.mock("../api", () => ({
  kanbanApi: {
    agents: vi.fn(async () => ({ agents: ["engineer", "analyst"] })),
    getModelOptions,
    refreshModelOptions,
    updateColumn,
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
  // clearAllMocks (not restoreAllMocks) — the vi.mock factory above installs
  // vi.fn() defaults once at module load. For a bare vi.fn() with no real
  // implementation to restore to, mockRestore() strips the factory default
  // (equivalent to mockReset()), and any subsequent test that relies on the
  // shared default silently breaks when an earlier test's afterEach runs.
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
