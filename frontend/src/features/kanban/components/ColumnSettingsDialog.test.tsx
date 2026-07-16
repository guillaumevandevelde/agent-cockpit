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
});
