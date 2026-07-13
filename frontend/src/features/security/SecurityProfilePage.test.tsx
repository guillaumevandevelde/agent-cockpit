// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectProvider } from "@/contexts/ProjectContext";
import type { SecurityProfile } from "./types";

// Mock the API module before importing the page so the component picks up
// the mocked functions. The mocks close over a seeded profile literal so
// they don't have to reach back to module-scope vi.fn() bindings (which
// would resolve to the wrong arity under vitest's hoisted mock factory).
vi.mock("./api", () => {
  const profile: SecurityProfile = {
    project_path: "/tmp/product-a",
    risk_class: "product-staging",
    default_transport: "sandcastle",
    default_skip_permissions: false,
    secrets_scope_id: null,
    resource_quota: {
      memory_mb: 1024,
      cpu_quota: 1,
      pids_limit: 128,
      disk_mb: 2048,
    },
    network_policy: "allowlist",
    egress_allowlist: ["pypi.org", "github.com"],
    created_at: "2026-01-01T00:00:00",
    updated_at: "2026-01-01T00:00:00",
  };

  return {
    getSecurityProfile: vi.fn(async () => profile),
    putSecurityProfile: vi.fn(
      async (_projectPath: string, payload: SecurityProfile) => payload
    ),
    patchSecurityProfile: vi.fn(
      async (_projectPath: string, payload: SecurityProfile) => payload
    ),
    deleteSecurityProfile: vi.fn(async (projectPath: string) => ({
      project_path: projectPath,
      deleted: true,
      recreated_default: profile,
    })),
  };
});

vi.mock("@/contexts/ProjectContext", async () => {
  const actual = await vi.importActual<typeof import("@/contexts/ProjectContext")>(
    "@/contexts/ProjectContext"
  );
  return {
    ...actual,
    useProjectContext: () => ({
      activeProject: {
        id: 1,
        name: "product-a",
        path: "/tmp/product-a",
        kind: "product",
        priority: null,
        is_active: true,
        last_accessed: "2026-01-01T00:00:00",
        created_at: "2026-01-01T00:00:00",
      },
      projects: [],
      loading: false,
      error: null,
      fetchProjects: vi.fn(),
      addProject: vi.fn(),
      removeProject: vi.fn(),
      discoverProjects: vi.fn(),
      setActiveProject: vi.fn(),
      clearActiveProject: vi.fn(),
    }),
  };
});

import { getSecurityProfile, patchSecurityProfile } from "./api";

const { SecurityProfilePage } = await import("./SecurityProfilePage");

beforeEach(() => {
  vi.mocked(getSecurityProfile).mockClear();
  vi.mocked(patchSecurityProfile).mockClear();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <ProjectProvider>
      <SecurityProfilePage />
    </ProjectProvider>
  );
}

describe("SecurityProfilePage", () => {
  it("loads the profile on mount and renders quota + egress fields", async () => {
    renderPage();
    await waitFor(() => expect(getSecurityProfile).toHaveBeenCalled());
    const stagingBadges = await screen.findAllByText("product-staging");
    expect(stagingBadges.length).toBeGreaterThan(0);
    const egressField = await screen.findByLabelText("egress_allowlist");
    expect((egressField as HTMLTextAreaElement).value).toBe(
      "pypi.org\ngithub.com"
    );
    expect(
      (screen.getByLabelText("memory_mb") as HTMLInputElement).value
    ).toBe("1024");
  });

  it("enables PATCH save when a quota field changes and submits on click", async () => {
    renderPage();
    const memoryInput = await screen.findByLabelText("memory_mb");
    fireEvent.change(memoryInput, { target: { value: "2048" } });
    expect((memoryInput as HTMLInputElement).value).toBe("2048");

    const patchBtn = await screen.findByRole("button", {
      name: /save changes \(patch\)/i,
    });
    expect(patchBtn.hasAttribute("disabled")).toBe(false);

    fireEvent.click(patchBtn);
    await waitFor(() =>
      expect(patchSecurityProfile).toHaveBeenCalledWith(
        "/tmp/product-a",
        expect.objectContaining({
          resource_quota: expect.objectContaining({ memory_mb: 2048 }),
        })
      )
    );
  });
});