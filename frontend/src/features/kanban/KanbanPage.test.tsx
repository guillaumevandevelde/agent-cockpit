// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { toast } from "sonner";
import type { Card } from "./types";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/contexts/ProjectContext", () => ({
  useProjectContext: () => ({
    activeProject: { path: "/proj", id: "1", name: "proj", is_active: true },
  }),
}));

vi.mock("@/contexts/ProviderContext", () => ({
  useProviderContext: () => ({
    providers: [],
    selectedProviderId: null,
  }),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  stub.projectKey = vi.fn(async () => ({ project_key: "proj-1" }));
  stub.listColumns = vi.fn(async () => ({ columns: [] }));
  stub.listCards = vi.fn(async () => ({ items: [] }));
  stub.listGates = vi.fn(async () => []);
  stub.activity = vi.fn(async () => []);
  stub.mcpHealth = vi.fn(async () => ({
    ok: true,
    advertised_endpoint: null,
    routes_to_mount: true,
    message_post_status: null,
    tool_call_ok: true,
    protocol_version: null,
    tools: [],
    db_ok: true,
    error: null,
  }));
  stub.mcpStatus = vi.fn(async () => ({ enabled: true }));
  stub.dispatchPause = vi.fn(async () => ({ paused: false, paused_until: null }));
  stub.getShipMode = vi.fn(async () => ({ mode: "direct" }));
  stub.getSkipPermissions = vi.fn(async () => ({ enabled: false }));
  stub.getAutodispatch = vi.fn(async () => ({ enabled: false }));
  stub.getDefaultTransport = vi.fn(async () => ({ transport: "tmux" }));
  return { kanbanApi: stub };
});

const { kanbanApi } = await import("./api");
const { default: KanbanPage } = await import("./KanbanPage");

const setHidden = (hidden: boolean) => {
  Object.defineProperty(document, "hidden", {
    value: hidden,
    configurable: true,
  });
};

afterEach(() => {
  cleanup();
  // clearAllMocks (not restoreAllMocks) — the vi.mock("./api", …) factory above
  // installs vi.fn() defaults once at module load. For a bare vi.fn() with no
  // real implementation, mockRestore() strips the factory default (equivalent
  // to mockReset()), silently breaking any subsequent test that relies on the
  // shared default. clearAllMocks() clears call history / results but
  // preserves the factory-installed implementation, so file order is no longer
  // load-bearing. See kanban card 3097ebadd3… for the full analysis.
  vi.clearAllMocks();
  setHidden(false);
});

function makeCard(overrides: Partial<Card> = {}): Card {
  return {
    id: "card-1",
    project_key: "proj-1",
    title: "Card One",
    description: "",
    column: "Backlog",
    rank: "0000",
    created_at: "2026-07-16T00:00:00Z",
    updated_at: "2026-07-16T00:00:00Z",
    deliverables: [],
    ...overrides,
  };
}

const BACKLOG_COLUMN: import("./types").KanbanColumn = {
  id: "col-backlog",
  project_key: "proj-1",
  name: "Backlog",
  rank: "0000",
  default_agent: null,
  default_provider: null,
  default_model: null,
  max_sessions: null,
  created_at: "2026-07-16T00:00:00Z",
  updated_at: "2026-07-16T00:00:00Z",
};

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderAt(initialEntry: string) {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationDisplay />
      <Routes>
        <Route path="/kanban" element={<KanbanPage />} />
      </Routes>
    </MemoryRouter>
  );
}

describe("KanbanPage live refresh", () => {
  it("refetches immediately when the tab regains visibility, not just on the next poll tick", async () => {
    renderAt("/kanban");

    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    setHidden(true);
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(kanbanApi.listCards).toHaveBeenCalledTimes(1);

    setHidden(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(2));
  });
});

describe("KanbanPage new-card dialog", () => {
  it("forwards analyst_agent_id and executor_agent_id to kanbanApi.createCard (multi-agent create path)", async () => {
    const createCardMock = kanbanApi.createCard as ReturnType<typeof vi.fn>;
    createCardMock.mockResolvedValue({});

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    // Open the New card dialog. Both analyst/executor defaults are AUTO,
    // which the dialog translates to null on submit.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "New card" }));
    });
    // Title is required — fill it.
    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Multi-agent card" },
    });
    await act(() => {
      screen.getByRole("button", { name: "Create" }).click();
    });

    await waitFor(() => expect(createCardMock).toHaveBeenCalledTimes(1));
    const body = createCardMock.mock.calls[0][0];
    // CardEditDialog already emits both fields with AUTO → null; the bug
    // was that KanbanPage.tsx's destructure + createCard body type dropped
    // them, so the keys were missing from the POST.
    expect(body).toHaveProperty("analyst_agent_id");
    expect(body).toHaveProperty("executor_agent_id");
    expect(body.analyst_agent_id).toBeNull();
    expect(body.executor_agent_id).toBeNull();
  });
});

describe("KanbanPage ?card= deep link", () => {
  it("opens the drawer for a card already present on the board", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard()],
    });

    renderAt("/kanban?card=card-1");

    expect(await screen.findByTestId("card-id-chip")).toBeInTheDocument();
    expect(kanbanApi.getCard).not.toHaveBeenCalled();
  });

  it("falls back to kanbanApi.getCard for a card from another project", async () => {
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({ items: [] });
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeCard({ id: "card-2", project_key: "other-proj", title: "Other Project Card" })
    );

    renderAt("/kanban?card=card-2");

    await waitFor(() => expect(kanbanApi.getCard).toHaveBeenCalledWith("card-2"));
    expect(await screen.findByText("Other Project Card")).toBeInTheDocument();
  });

  it("shows an error toast and strips the param for an unknown card id", async () => {
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({ items: [] });
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("404"));

    const { getByTestId } = renderAt("/kanban?card=missing-card");

    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    await waitFor(() => expect(getByTestId("location").textContent).toBe("/kanban"));
    expect(screen.queryByTestId("card-id-chip")).not.toBeInTheDocument();
  });

  it("opening a card updates the URL and closing it removes the param", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [makeCard()],
    });

    const { getByTestId } = renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    await act(async () => {
      fireEvent.click(screen.getByText("Card One"));
    });
    await screen.findByTestId("card-id-chip");
    expect(getByTestId("location").textContent).toBe("/kanban?card=card-1");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Close" }));
    });
    await waitFor(() => expect(screen.queryByTestId("card-id-chip")).not.toBeInTheDocument());
    expect(getByTestId("location").textContent).toBe("/kanban");
  });
});

describe("KanbanPage ready-state precedence", () => {
  const DONE_COLUMN: import("./types").KanbanColumn = {
    ...BACKLOG_COLUMN,
    id: "col-done",
    name: "Done",
  };
  const IMPEDIMENT_COLUMN: import("./types").KanbanColumn = {
    ...BACKLOG_COLUMN,
    id: "col-impediment",
    name: "Impediment",
  };

  it("applies completed > impeded > in_progress > missing_dep > dependent > ready precedence", async () => {
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [BACKLOG_COLUMN, DONE_COLUMN, IMPEDIMENT_COLUMN],
    });
    (kanbanApi.listCards as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [
        // A stale agent claim in Done must still read "completed", not
        // "in_progress" — column-based terminal states win over the claim.
        makeCard({ id: "card-done", column: "Done", claimed_by: "agent:tmux-x" }),
        makeCard({ id: "card-impeded", column: "Impediment", claimed_by: "agent:tmux-y" }),
        makeCard({ id: "card-in-progress", claimed_by: "agent:tmux-z" }),
        // Dep on a card that no longer exists → permanent "missing_dep" block,
        // distinct from a live non-Done sibling (dangling-depends-on-analyse.md).
        makeCard({ id: "card-missing-dep", depends_on: ["deleted-parent"] }),
        // Dep on a live, non-Done sibling → temporary "dependent" block.
        makeCard({ id: "card-live-parent", column: "Backlog" }),
        makeCard({ id: "card-dependent", depends_on: ["card-live-parent"] }),
        makeCard({ id: "card-ready" }),
      ],
    });

    renderAt("/kanban");
    await waitFor(() => expect(kanbanApi.listCards).toHaveBeenCalledTimes(1));

    const stateOf = (cardId: string) =>
      document
        .querySelector(`[data-card-id="${cardId}"]`)
        ?.querySelector("[data-ready-state]")
        ?.getAttribute("data-ready-state");

    await waitFor(() => expect(stateOf("card-done")).toBe("completed"));
    expect(stateOf("card-impeded")).toBe("impeded");
    expect(stateOf("card-in-progress")).toBe("in_progress");
    expect(stateOf("card-missing-dep")).toBe("missing_dep");
    expect(stateOf("card-dependent")).toBe("dependent");
    expect(stateOf("card-ready")).toBe("ready");
  });
});