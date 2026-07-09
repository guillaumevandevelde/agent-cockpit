// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({
    providers: [],
    selectedProviderId: null,
  }),
}));

vi.mock("@/features/cc-bridge/api", () => ({
  fetchResumableSessions: vi.fn(async () => ({ sessions: [] })),
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
});
