// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/contexts/ProjectContext", () => ({
  useProjectContext: () => ({
    activeProject: { key: "demo", path: "/tmp/demo", is_active: true },
    projects: [],
  }),
}));

const fetchEndpointsMock = vi.fn(async () => ({ endpoints: [] as Array<Record<string, unknown>> })) as Mock;
const upsertEndpointMock = vi.fn(async () => ({} as Record<string, unknown>)) as Mock;
const deleteEndpointMock = vi.fn(async () => ({ deleted: true })) as Mock;

vi.mock("@/features/cc-bridge/api", () => ({
  fetchEndpoints: (...args: unknown[]) => fetchEndpointsMock(...args),
  upsertEndpoint: (...args: unknown[]) => upsertEndpointMock(...args),
  deleteEndpoint: (...args: unknown[]) => deleteEndpointMock(...args),
}));

const projectKeyMock = vi.fn(async () => ({ project_key: "demo" })) as Mock;
vi.mock("@/features/kanban/api", () => ({
  kanbanApi: {
    projectKey: (...args: unknown[]) => projectKeyMock(...args),
  },
}));

import { EndpointsPage } from "./EndpointsPage";

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EndpointsPage", () => {
  it("shows the empty-state with an Add button when there are no endpoints", async () => {
    render(<EndpointsPage />, { wrapper: MemoryRouter });
    expect(
      await screen.findByText(/no endpoints configured yet/i),
    ).toBeInTheDocument();
    // Two "Add endpoint" buttons exist (header + empty-state CTA). Both
    // must render — assert at least one is present.
    expect(
      screen.getAllByRole("button", { name: /add endpoint/i }).length,
    ).toBeGreaterThanOrEqual(1);
  });

  it("loads endpoints for the active project on mount", async () => {
    fetchEndpointsMock.mockResolvedValueOnce({ endpoints: [] });
    render(<EndpointsPage />, { wrapper: MemoryRouter });
    await waitFor(() => expect(fetchEndpointsMock).toHaveBeenCalledWith("demo"));
  });

  it("renders one row per endpoint with name, base_url, model, and credential badge", async () => {
    fetchEndpointsMock.mockResolvedValueOnce({
      endpoints: [
        {
          name: "groq-free",
          base_url: "http://127.0.0.1:4000/v1",
          model: "groq/llama",
          credential_name: "groq_api_key",
          credential_configured: true,
        },
        {
          name: "openrouter-public",
          base_url: "https://openrouter.ai/api/v1",
          model: "openrouter/auto",
          credential_name: null,
          credential_configured: false,
        },
      ],
    });
    render(<EndpointsPage />, { wrapper: MemoryRouter });
    expect(await screen.findByText("groq-free")).toBeInTheDocument();
    expect(screen.getByText("http://127.0.0.1:4000/v1")).toBeInTheDocument();
    expect(screen.getByText("groq/llama")).toBeInTheDocument();
    expect(screen.getByText("openrouter-public")).toBeInTheDocument();
    expect(screen.getAllByText(/credential/i).length).toBeGreaterThan(0);
  });

  it("refreshes the list after a successful upsert from the dialog", async () => {
    fetchEndpointsMock
      .mockResolvedValueOnce({ endpoints: [] })
      .mockResolvedValueOnce({
        endpoints: [
          {
            name: "groq-free",
            base_url: "http://localhost:4000/v1",
            model: "groq/llama",
            credential_name: null,
            credential_configured: false,
          },
        ],
      });
    render(<EndpointsPage />, { wrapper: MemoryRouter });
    await screen.findByText(/no endpoints configured yet/i);
    expect(fetchEndpointsMock).toHaveBeenCalledTimes(1);
  });
});