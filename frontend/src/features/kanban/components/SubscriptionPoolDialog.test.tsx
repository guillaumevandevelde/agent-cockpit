// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/features/cc-bridge/api", () => ({
  fetchEndpoints: vi.fn(async () => ({ endpoints: [] })),
}));

vi.mock("../api", () => ({
  kanbanApi: {
    getSubscriptionPool: vi.fn(),
    setSubscriptionPool: vi.fn(),
    getActiveSubscriptionOverride: vi.fn(),
    setActiveSubscriptionOverride: vi.fn(),
    dispatchPause: vi.fn(),
    setSubscriptionPause: vi.fn(),
  },
}));

const { kanbanApi } = await import("../api");
const { fetchEndpoints } = await import("@/features/cc-bridge/api");
const { toast } = await import("sonner");
const { SubscriptionPoolDialog } = await import("./SubscriptionPoolDialog");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PK = "git:example.com/me/repo";

async function flushLoads() {
  await waitFor(() => {
    expect(kanbanApi.getSubscriptionPool).toHaveBeenCalledWith(PK);
    expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalledWith(PK);
  });
}

function mockUnsetPoolAndOverride() {
  (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
    .mockResolvedValue({ project_key: PK, pool: null });
  (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
    .mockResolvedValue({ project_key: PK, override: null });
  // Kaart f056b2888a…: dialog also reads dispatch-pause to seed the manual
  // pause toggle state. Default = no manual pauses so the existing tests
  // keep showing the un-paused state.
  (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
    paused: false,
    paused_until: null,
    paused_providers: [],
    manually_paused_providers: [],
  });
}

describe("SubscriptionPoolDialog — precedence header", () => {
  it("renders the precedence chain header describing the effective chain", async () => {
    mockUnsetPoolAndOverride();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // Each phrase appears in *both* the DialogDescription (precedence chain)
    // and elsewhere in the dialog — assert "appears somewhere" rather than
    // "exactly one", which would be brittle under legitimate reuse.
    expect(screen.getAllByText(/global override/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/pool/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/per-card/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/column defaults/i).length).toBeGreaterThan(0);
  });
});

describe("SubscriptionPoolDialog — override section", () => {
  it("shows 'no override' state when override is null", async () => {
    mockUnsetPoolAndOverride();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // The override Select's trigger shows the same string as one of its
    // SelectItem options, so a `getByText` would match both. The
    // aria-live state span has a unique testid for the test to anchor on.
    expect(screen.getByTestId("override-state")).toHaveTextContent(
      /subscription: column defaults/i
    );
  });

  it("shows the pinned provider as the current override state", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    expect(screen.getByTestId("override-state")).toHaveTextContent(
      /subscription: minimax/i
    );
  });

  it("clear-override control POSTs null and announces success", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    (kanbanApi.setActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // Clear-override has aria-label "Clear override" to disambiguate from any
    // pool Clear button.
    const clearBtn = screen.getByRole("button", { name: /^clear override$/i });
    fireEvent.click(clearBtn);
    await waitFor(() =>
      expect(kanbanApi.setActiveSubscriptionOverride).toHaveBeenCalledWith(PK, null)
    );
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/cleared/i),
    );
  });
});

describe("SubscriptionPoolDialog — pool section", () => {
  it("shows the empty-state row and Add-first-subscription control when pool is null", async () => {
    mockUnsetPoolAndOverride();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    expect(screen.getByText(/no subscription pool configured/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /add first subscription/i }),
    ).toBeInTheDocument();
  });

  it("renders existing pool entries with their provider labels", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { provider: "anthropic", model: null, drempel: 0.9 },
          { provider: "minimax", model: "MiniMax-M3[1m]", drempel: 0.95 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled(),
    );
    // Provider labels appear in the SelectItem list (rendered into a portal,
    // not in the visible select trigger by default). Use getAllByText so we
    // match across both the trigger + dropdown options without flake.
    expect(screen.getAllByText(/anthropic/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/minimax/i).length).toBeGreaterThan(0);
  });

  it("Add-first-subscription creates a default entry and POSTs it", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [{ provider: "anthropic", model: null, drempel: 0.9 }],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    fireEvent.click(
      screen.getByRole("button", { name: /add first subscription/i }),
    );
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, [
        { provider: "anthropic", model: null, endpoint_name: null, drempel: 0.9 },
      ]),
    );
  });

  it("Clear-pool POSTs null and announces success", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        project_key: PK,
        pool: [{ provider: "anthropic", model: null, drempel: 0.9 }],
      });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // Pool Clear is aria-labelled "Clear pool" so it doesn't collide with the
    // override Clear control. Title is "Clear the pool".
    const clearBtn = screen.getByRole("button", { name: /^clear pool$/i });
    fireEvent.click(clearBtn);
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, null),
    );
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/pool.*cleared/i),
    );
  });

  it("shows the project endpoint selector for an anthropic-compatible entry", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [
        {
          provider: "anthropic-compatible",
          model: null,
          endpoint_name: "groq-free",
          drempel: 0.9,
        },
      ],
    });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: [],
    });
    (fetchEndpoints as ReturnType<typeof vi.fn>).mockResolvedValue({
      endpoints: [
        {
          name: "groq-free",
          base_url: "http://127.0.0.1:4000/v1",
          model: "groq/llama",
          credential_name: "groq_api_key",
          credential_configured: true,
        },
      ],
    });

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );

    const endpoint = await screen.findByRole("combobox", {
      name: "Endpoint for pool entry 1",
    });
    expect(endpoint).toHaveTextContent("groq-free");
    expect(fetchEndpoints).toHaveBeenCalledWith(PK);
  });

  it("atomically picks the first endpoint when changing an entry to anthropic-compatible", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [{ provider: "anthropic", model: null, endpoint_name: null, drempel: 0.9 }],
    });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [],
    });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: [],
    });
    (fetchEndpoints as ReturnType<typeof vi.fn>).mockResolvedValue({
      endpoints: [
        {
          name: "groq-free",
          base_url: "http://127.0.0.1:4000/v1",
          model: "groq/llama",
          credential_name: null,
          credential_configured: false,
        },
      ],
    });

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    const provider = await screen.findByRole("combobox", {
      name: "Provider for pool entry 1",
    });
    fireEvent.click(provider);
    fireEvent.click(screen.getByRole("option", { name: "Compatible endpoint" }));

    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, [
        {
          provider: "anthropic-compatible",
          model: null,
          endpoint_name: "groq-free",
          drempel: 0.9,
        },
      ]),
    );
  });

  it("clears endpoint_name when changing a compatible entry to another provider", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [
        {
          provider: "anthropic-compatible",
          model: null,
          endpoint_name: "groq-free",
          drempel: 0.9,
        },
      ],
    });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [],
    });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: [],
    });
    (fetchEndpoints as ReturnType<typeof vi.fn>).mockResolvedValue({
      endpoints: [
        {
          name: "groq-free",
          base_url: "http://127.0.0.1:4000/v1",
          model: "groq/llama",
          credential_name: null,
          credential_configured: false,
        },
      ],
    });

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    const provider = await screen.findByRole("combobox", {
      name: "Provider for pool entry 1",
    });
    fireEvent.click(provider);
    fireEvent.click(screen.getByRole("option", { name: "Anthropic" }));

    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, [
        {
          provider: "anthropic",
          model: null,
          endpoint_name: null,
          drempel: 0.9,
        },
      ]),
    );
    expect(
      screen.queryByRole("combobox", { name: "Endpoint for pool entry 1" }),
    ).toBeNull();
  });

  it("links the compatible-entry empty state to endpoint management", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>).mockResolvedValue({
      project_key: PK,
      pool: [
        {
          provider: "anthropic-compatible",
          model: null,
          endpoint_name: "deleted-endpoint",
          drempel: 0.9,
        },
      ],
    });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: [],
    });
    (fetchEndpoints as ReturnType<typeof vi.fn>).mockResolvedValue({ endpoints: [] });

    render(
      <MemoryRouter>
        <SubscriptionPoolDialog
          open
          projectKey={PK}
          onClose={() => {}}
          onChanged={() => {}}
        />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText(/geen endpoints geconfigureerd/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /naar endpoints/i }),
    ).toHaveAttribute("href", "/endpoints");
  });
});


describe("SubscriptionPoolDialog — override interaction with pool", () => {
  it("renders the override-active rule when an override is set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { provider: "anthropic", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // The rule makes the silent-disable from §1.2 of the analysis visible:
    // "override actief → pool staat uit".
    expect(screen.getByText(/override.*pool.*uit/i)).toBeInTheDocument();
  });

  it("disables pool add control when an override is active", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { provider: "anthropic", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // The "Add subscription" button is aria-disabled when the pool can't
    // actually be consulted (because the override wins); the user can still
    // see the entries but cannot mutate them.
    const addBtn = screen.getByRole("button", { name: /add subscription/i });
    expect(addBtn).toBeDisabled();
  });

  it("hides the override-active rule when no override is set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { provider: "anthropic", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    expect(screen.queryByText(/override.*pool.*uit/i)).not.toBeInTheDocument();
  });
});

describe("SubscriptionPoolDialog — close", () => {
  it("Close button calls onClose and triggers reload of both endpoints", async () => {
    mockUnsetPoolAndOverride();
    const onClose = vi.fn();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={onClose}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // The Radix DialogContent adds its own sr-only "Close" X-button in the
    // top-right; the testid anchors to the explicit Close button in our
    // DialogFooter.
    fireEvent.click(screen.getByTestId("close-dialog"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});


describe("SubscriptionPoolDialog — manual subscription pause (kaart f056b2888a…)", () => {
  it("renders one Pause button per provider, all in the un-paused state by default", async () => {
    mockUnsetPoolAndOverride();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    // Wait for the pause section's GET to land.
    await waitFor(() =>
      expect(kanbanApi.dispatchPause).toHaveBeenCalled()
    );
    // One Pause button per provider.
    for (const label of ["Anthropic", "Bedrock", "MiniMax"]) {
      expect(
        screen.getByRole("button", { name: `Pause dispatch on ${label}` })
      ).toBeTruthy();
    }
    // And no Resume buttons yet — nothing is paused.
    expect(screen.queryByRole("button", { name: /Resume dispatch on/i })).toBeNull();
  });

  it("shows the Resume button for a provider the operator has already paused", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: ["anthropic"],
    });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await flushLoads();
    expect(
      await screen.findByRole("button", { name: "Resume dispatch on Anthropic" })
    ).toBeTruthy();
    // Other providers still show Pause (only the toggled-on one is paused).
    expect(
      screen.getByRole("button", { name: "Pause dispatch on Bedrock" })
    ).toBeTruthy();
  });

  it("clicking Pause calls setSubscriptionPause(paused=true) and toasts on success", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.setSubscriptionPause as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        provider: "anthropic",
        paused: true,
        manually_paused_providers: ["anthropic"],
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={vi.fn()}
      />,
    );
    await flushLoads();

    fireEvent.click(
      screen.getByRole("button", { name: "Pause dispatch on Anthropic" })
    );

    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPause).toHaveBeenCalledWith("anthropic", true)
    );
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
  });

  it("rolls back the optimistic toggle when setSubscriptionPause fails", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.setSubscriptionPause as ReturnType<typeof vi.fn>)
      .mockRejectedValue(new Error("nope"));
    const onChanged = vi.fn();
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={onChanged}
      />,
    );
    await flushLoads();

    // Click Pause -> the button should briefly flip to Resume (optimistic),
    // then flip back after the error.
    fireEvent.click(
      screen.getByRole("button", { name: "Pause dispatch on Anthropic" })
    );
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPause).toHaveBeenCalled()
    );
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    // The Anthropic row is back to the un-paused state with a Pause button.
    expect(
      screen.getByRole("button", { name: "Pause dispatch on Anthropic" })
    ).toBeTruthy();
    // The error path must NOT signal the parent's onChanged (no false-positive
    // reload trigger on a failure).
    expect(onChanged).not.toHaveBeenCalled();
  });
});
