// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

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
  vi.clearAllMocks();
  vi.restoreAllMocks();
  setHidden(false);
});

describe("KanbanPage live refresh", () => {
  it("refetches immediately when the tab regains visibility, not just on the next poll tick", async () => {
    render(<KanbanPage />);

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

    render(<KanbanPage />);
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