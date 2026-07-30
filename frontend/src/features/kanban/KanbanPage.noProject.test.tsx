// @vitest-environment jsdom
//
// Separate file (not a case inside KanbanPage.test.tsx) because the
// no-project state is selected by the `vi.mock("@/contexts/ProjectContext")`
// factory, which is hoisted and module-scoped — one mock shape per file.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/contexts/ProjectContext", () => ({
  useProjectContext: () => ({ activeProject: null }),
}));

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({ providers: [], selectedProviderId: null }),
}));

const { default: KanbanPage } = await import("./KanbanPage");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("KanbanPage — no active project", () => {
  it("still renders the page heading alongside the select-a-project prompt", () => {
    render(
      <MemoryRouter>
        <KanbanPage />
      </MemoryRouter>,
    );

    // The e2e smoke test asserts `main h1` contains "Kanban". This state used
    // to return a bare div with no heading at all, so on a fresh backend with
    // no active project — exactly what CI provisions — the page had no <h1>
    // and `kanban board loads` failed. Every other page keeps its heading in
    // its empty state; pin that Kanban does too.
    expect(
      screen.getByRole("heading", { level: 1, name: "Kanban" }),
    ).toBeTruthy();
    expect(screen.getByText("Select a project first.")).toBeTruthy();
  });
});
