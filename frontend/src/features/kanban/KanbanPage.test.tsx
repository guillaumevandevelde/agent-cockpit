// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, waitFor } from "@testing-library/react";

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
    tools: [],
    db_ok: true,
    error: null,
  }));
  stub.mcpStatus = vi.fn(async () => ({ enabled: true }));
  stub.dispatchPause = vi.fn(async () => ({ paused: false, paused_until: null }));
  stub.getShipMode = vi.fn(async () => ({ mode: "direct" }));
  stub.getSkipPermissions = vi.fn(async () => ({ enabled: false }));
  stub.getAutodispatch = vi.fn(async () => ({ enabled: false }));
  stub.getMaxSessions = vi.fn(async () => ({ max_sessions: 1 }));
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
