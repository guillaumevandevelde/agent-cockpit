// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({
    providers: [],
    selectedProviderId: null,
  }),
}));

vi.mock("@/features/cc-bridge/api", () => ({
  fetchResumableSessions: vi.fn(async () => ({ sessions: [] })),
}));

const listColumnsMock = vi.fn(async () => ({
  columns: [
    { id: "c1", name: "engineer", default_agent: "engineer", default_provider: "minimax", default_model: "m3" },
    { id: "c2", name: "analyst", default_agent: "analyst", default_provider: null, default_model: null },
    // Flow column with no default_agent -> should not get an override row.
    { id: "c3", name: "Backlog", default_agent: null, default_provider: null, default_model: null },
  ],
}));

vi.mock("../api", () => ({
  kanbanApi: {
    getModelOptions: vi.fn(async () => ({ provider: "claude-code", options: ["sonnet", "opus", "haiku"] })),
    listColumns: (...args: unknown[]) => listColumnsMock(...(args as [])),
  },
}));

import { CardEditDialog } from "./CardEditDialog";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("CardEditDialog", () => {
  it("does not render a Column selector (new cards always go to Backlog)", () => {
    render(
      <CardEditDialog
        open
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    // A standalone <Label>Column</Label> used to live above the column
    // dropdown. After the change, the literal text "Column" should not
    // appear as a field label anywhere in the dialog (column names like
    // "Doing" can still appear inside option lists, so we anchor on the
    // <label> element specifically).
    expect(screen.queryByText("Column", { selector: "label" })).toBeNull();
  });

  it("places Work type before Title, so it's the first routing decision", () => {
    render(
      <CardEditDialog
        open
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    // Labels with `htmlFor` map to the only matching <label for="...">
    const workTypeLabel = screen.getByText("Work type", { selector: "label" });
    const titleLabel = screen.getByText("Title", { selector: "label" });
    expect(
      workTypeLabel.compareDocumentPosition(titleLabel) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("does not include `column` in the onSubmit payload (the column is irrelevant now)", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    // The Create button is disabled until a title is set, so type one first.
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "New card" },
    });
    screen.getByRole("button", { name: /create/i }).click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).not.toHaveProperty("column");
  });

  it("forwards the chosen work_type in the onSubmit payload", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    screen.getByRole("button", { name: /update/i }).click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    // unchanged from unset → null
    expect(payload).toHaveProperty("work_type", null);
    expect(payload).not.toHaveProperty("column");
  });

  it("forwards the chosen model in the onSubmit payload", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Model"), { target: { value: "opus" } });
    screen.getByRole("button", { name: /update/i }).click();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toHaveProperty("model", "opus");
  });

  it("submits model: null when the field is left empty", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    screen.getByRole("button", { name: /update/i }).click();
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toHaveProperty("model", null);
  });

  it("renders one override row per agent column (skipping flow columns) and forwards column_overrides", async () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        projectKey="proj"
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    // Rows appear once listColumns resolves. Only agent columns get a row.
    const engineerModel = await screen.findByLabelText("Model for engineer");
    expect(screen.getByLabelText("Model for analyst")).toBeTruthy();
    expect(screen.queryByLabelText("Model for Backlog")).toBeNull();
    // Column default shows as the placeholder.
    expect((engineerModel as HTMLInputElement).placeholder).toBe("m3");

    fireEvent.change(engineerModel, { target: { value: "sonnet-5" } });
    screen.getByRole("button", { name: /update/i }).click();

    const payload = onSubmit.mock.calls[0][0];
    // provider left at Default -> null; model is the typed override.
    expect(payload.column_overrides).toEqual({
      engineer: { model: "sonnet-5", provider: null },
    });
  });

  it("submits column_overrides: null when no override row is filled", async () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        projectKey="proj"
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    await screen.findByLabelText("Model for engineer");
    screen.getByRole("button", { name: /update/i }).click();
    expect(onSubmit.mock.calls[0][0]).toHaveProperty("column_overrides", null);
  });

  it("does not render the multi-agent split section on a new card", () => {
    render(
      <CardEditDialog
        open
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    // New card (no `initial`) is single-agent only — the advanced split
    // toggle should not exist.
    expect(screen.queryByText(/Multi-agent split/i)).toBeNull();
  });

  it("still renders the multi-agent split section when editing an existing card", () => {
    render(
      <CardEditDialog
        open
        initial={{ title: "T", description: "" }}
        onClose={() => {}}
        onSubmit={() => {}}
      />,
    );
    expect(screen.getByText(/Multi-agent split/i)).toBeTruthy();
  });

  it("still forwards analyst/executor ids (null) on a new card submit", () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "New card" },
    });
    screen.getByRole("button", { name: /create/i }).click();
    const payload = onSubmit.mock.calls[0][0];
    expect(payload).toHaveProperty("analyst_agent_id", null);
    expect(payload).toHaveProperty("executor_agent_id", null);
  });

  it("pre-fills existing column_overrides from initial", async () => {
    const onSubmit = vi.fn();
    render(
      <CardEditDialog
        open
        projectKey="proj"
        initial={{
          title: "T",
          description: "",
          column_overrides: { engineer: { model: "opus", provider: "anthropic" } },
        }}
        onClose={() => {}}
        onSubmit={onSubmit}
      />,
    );
    const engineerModel = (await screen.findByLabelText("Model for engineer")) as HTMLInputElement;
    await waitFor(() => expect(engineerModel.value).toBe("opus"));
    // Round-trips unchanged on submit.
    screen.getByRole("button", { name: /update/i }).click();
    expect(onSubmit.mock.calls[0][0].column_overrides).toEqual({
      engineer: { model: "opus", provider: "anthropic" },
    });
  });
});
