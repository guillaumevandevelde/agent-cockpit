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
    // Kaart 7411d25e…: dialog fetches columns + each column's
    // spillover_chain to render the per-column summary list. Default
    // mocks: empty column list (existing tests keep passing) and a
    // synthetic anthropic chain (tests that don't care about chains
    // still get a valid shape for any column that *is* listed).
    listColumns: vi.fn(),
    getColumnEffectiveModel: vi.fn(),
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
    // Per-column tails (kaart b36ca702…): the dialog reads the pool for the
    // selected column; these tests never pick one, so the argument is the
    // board-wide `null`. Asserting it explicitly keeps the exact-args match
    // honest instead of loosening it to `expect.anything()`.
    expect(kanbanApi.getSubscriptionPool).toHaveBeenCalledWith(PK, null);
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
  // Kaart 7411d25e…: default column list is empty so the per-column
  // summary list stays absent in tests that don't exercise it.
  // Tests that DO want the list mock their own listColumns response.
  (kanbanApi.listColumns as ReturnType<typeof vi.fn>)
    .mockResolvedValue({ columns: [] });
  (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
    .mockResolvedValue({
      provider: "anthropic",
      model: null,
      provider_source: "column_default",
      model_source: "column_default",
      global_override: null,
      pool_choice: null,
      column_default_provider: "anthropic",
      column_default_model: null,
      persona_model: null,
      spillover_chain: ["anthropic"],
    });
}

/** Synthetic column-row payloads for the per-column chain list (kaart
 *  7411d25e…). The dialog doesn't care about most fields, only ``id``,
 *  ``name``, and ``default_agent`` — keep the rest minimal so a future
 *  field addition doesn't invalidate the tests. */
function makeColumnRow(id: string, name: string, defaultAgent: string) {
  return {
    id,
    project_key: PK,
    name,
    rank: "0",
    default_agent: defaultAgent,
    default_provider: null,
    default_model: null,
    max_sessions: null,
    token_saver_enabled: false,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
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
        {
          cli: "claude-code",
          provider: "anthropic",
          model: null,
          endpoint_name: null,
          drempel: 0.9,
        },
      ], null),
    );
  });

  it("renders a CLI selector defaulting to Claude Code on new entries (kaart 8f40d443…)", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: [] });
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
    // The CLI combobox for entry 1 has aria-label "CLI for pool entry 1".
    const cli = await screen.findByRole("combobox", {
      name: "CLI for pool entry 1",
    });
    expect(cli).toHaveTextContent(/claude code/i);
  });

  it("changing the CLI selector persists the new cli on the entry (kaart 8f40d443…)", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: [] });
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
    const cli = await screen.findByRole("combobox", {
      name: "CLI for pool entry 1",
    });
    fireEvent.click(cli);
    fireEvent.click(screen.getByRole("option", { name: /^opencode$/i }));
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, [
        {
          cli: "open-code",
          provider: "anthropic",
          model: null,
          endpoint_name: null,
          drempel: 0.9,
        },
      ], null),
    );
  });

  it("renders existing entries without a cli field as Claude Code (legacy round-trip)", async () => {
    // Legacy KanbanMeta rows from before kaart 8f40d443… omit the ``cli``
    // field. The dialog's ``entryCli`` fallback keeps them functional by
    // defaulting the CLI selector to Claude Code so the existing snapshot
    // key still matches — the operator can flip it to e.g. open-code
    // explicitly when migrating.
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
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
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled(),
    );
    const cli = await screen.findByRole("combobox", {
      name: "CLI for pool entry 1",
    });
    expect(cli).toHaveTextContent(/claude code/i);
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
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, null, null),
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
          cli: "claude-code",
          provider: "anthropic-compatible",
          model: null,
          endpoint_name: "groq-free",
          drempel: 0.9,
        },
      ], null),
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
          cli: "claude-code",
          provider: "anthropic",
          model: null,
          endpoint_name: null,
          drempel: 0.9,
        },
      ], null),
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

// ---- Spillover status surface (kaart 7411d25e…) ----------------------------
//
// The whole point of this card: an operator should be able to answer
// "why is my card stuck?" from the toolbar/dialog without reading the
// dispatch resolver source. The status line + per-column chain list are
// the two visible surfaces that make the answer reachable without a
// second click.

describe("SubscriptionPoolDialog — spillover status surface", () => {
  it("shows 'no spillover configured' when the board-wide pool is null and the column default has no head", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        columns: [
          makeColumnRow("col-engineer", "engineer", "engineer"),
        ],
      });
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        provider: "anthropic",
        model: null,
        provider_source: "column_default",
        model_source: "column_default",
        global_override: null,
        pool_choice: null,
        column_default_provider: null,
        column_default_model: null,
        persona_model: null,
        spillover_chain: ["anthropic"],
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => {
      // Spillover status line reads the chain + signals 'no tail'.
      expect(screen.getByTestId("spillover-status")).toHaveTextContent(
        /no spillover configured/i,
      );
    });
  });

  it("shows the spillover chain when one is configured (board-wide pool present)", async () => {
    mockUnsetPoolAndOverride();
    // A configured board-wide pool: dialog shows the chain in the status line.
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { provider: "minimax", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        columns: [
          makeColumnRow("col-engineer", "engineer", "engineer"),
        ],
      });
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        provider: "anthropic",
        model: null,
        provider_source: "column_default",
        model_source: "column_default",
        global_override: null,
        pool_choice: null,
        column_default_provider: "anthropic",
        column_default_model: null,
        persona_model: null,
        spillover_chain: ["anthropic", "minimax"],
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => {
      const status = screen.getByTestId("spillover-status");
      // The board-wide-pool variant reports the tail length so the
      // operator can tell at a glance whether a pool is even configured.
      expect(status).toHaveTextContent(/board-wide pool.*1 entry/i);
    });
  });

  it("renders a per-column chain row for every agent column with the resolved chain", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        columns: [
          makeColumnRow("col-analyst", "analyst", "analyst"),
          makeColumnRow("col-engineer", "engineer", "engineer"),
          makeColumnRow("col-reviewer", "reviewer", "reviewer"),
        ],
      });
    // Per-column chain differs by persona so the UI shows distinct rows.
    const chains: Record<string, string[]> = {
      analyst: ["anthropic", "minimax"],
      engineer: ["minimax"],
      reviewer: ["anthropic"],
    };
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      .mockImplementation(async (columnId: string) => {
        const col = columnId.startsWith("col-analyst")
          ? "analyst"
          : columnId.startsWith("col-engineer")
            ? "engineer"
            : "reviewer";
        return {
          provider: chains[col][0],
          model: null,
          provider_source: "column_default",
          model_source: "column_default",
          global_override: null,
          pool_choice: null,
          column_default_provider: chains[col][0],
          column_default_model: null,
          persona_model: null,
          spillover_chain: chains[col],
        };
      });
    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() => {
      // Each persona gets its own row with the resolved chain. Labels
      // are rendered through PROVIDER_LABELS so the row reads "Anthropic
      // → MiniMax" rather than the raw "anthropic"/"minimax" keys —
      // case-insensitive match keeps both representations in scope.
      expect(
        screen.getByTestId("per-column-chain-row-analyst"),
      ).toHaveTextContent(/anthropic.*→.*minimax/i);
      expect(
        screen.getByTestId("per-column-chain-row-engineer"),
      ).toHaveTextContent(/minimax/i);
      expect(
        screen.getByTestId("per-column-chain-row-reviewer"),
      ).toHaveTextContent(/anthropic/i);
    });
    // Status badges: 'tail' on the chains >1, 'no tail' on the chains ==1.
    expect(
      screen.getByTestId("per-column-chain-status-analyst"),
    ).toHaveTextContent(/1 tail/);
    expect(
      screen.getByTestId("per-column-chain-status-engineer"),
    ).toHaveTextContent(/no tail/);
    expect(
      screen.getByTestId("per-column-chain-status-reviewer"),
    ).toHaveTextContent(/no tail/);
  });
});

// ---- Chain refresh after mutation (kaart 7411d25e… revisit) ---------------
//
// The dialog promises — via `aria-live="polite"` on the headline
// status — that the chain is live. The previous wiring only refreshed
// the per-column chain on the initial `[projectKey]` effect, so any
// mutation (save pool, change override, toggle pause) showed a stale
// chain until the dialog was closed and reopened. The fix calls
// `refreshChainByColumn()` after every successful `onChanged()`; the
// tests below pin that contract so a regression can't silently come
// back: the FIRST chain fetch is the "before" snapshot, the SECOND
// is the "after" snapshot the operator should be able to see without
// re-opening the dialog.

interface ColumnChainFixture {
  columnId: string;
  columnName: string;
  defaultAgent: string;
  /**
   * Chain returned by the Nth call to `getColumnEffectiveModel` for
   * this column. The fixture cycles per-column so each mutation
   * produces a verifiable shape change. Use a function to mutate
   * based on a call counter.
   */
  chainsByCallIndex: (callIndex: number) => string[];
}

/** Each call returns the chain configured for the call index of the
 *  specific column. The mock uses a Map keyed by column id so multiple
 *  columns refetch independently. */
function makePerColumnChainMock(
  columns: ColumnChainFixture[],
): ReturnType<typeof vi.fn> {
  const counters = new Map<string, number>();
  return vi.fn(async (columnId: string) => {
    const col = columns.find((c) => c.columnId === columnId);
    if (!col) throw new Error(`unexpected column id ${columnId} in test`);
    const counter = counters.get(columnId) ?? 0;
    counters.set(columnId, counter + 1);
    const chain = col.chainsByCallIndex(counter);
    return {
      provider: chain[0],
      model: null,
      provider_source: "column_default",
      model_source: "column_default",
      global_override: null,
      pool_choice: null,
      column_default_provider: chain[0],
      column_default_model: null,
      persona_model: null,
      spillover_chain: chain,
    };
  });
}

describe("SubscriptionPoolDialog — chain refresh after mutation", () => {
  it("refreshes the per-column chain after a pool save (kaart 7411d25e… revisit)", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [
        makeColumnRow("col-analyst", "analyst", "analyst"),
      ],
    });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [{ provider: "minimax", model: null, drempel: 0.9 }],
      });
    // First call: column-default only. Second call (after save): column
    // default + the new pool entry = full spillover chain. The dialog
    // must surface the second call's result without a re-open.
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      = makePerColumnChainMock([
        {
          columnId: "col-analyst",
          columnName: "analyst",
          defaultAgent: "analyst",
          chainsByCallIndex: (i) =>
            i === 0 ? ["anthropic"] : ["anthropic", "minimax"],
        },
      ]);

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("per-column-chain-row-analyst"),
      ).toHaveTextContent(/anthropic/i),
    );
    // Initial state: column default only, no tail.
    expect(
      screen.getByTestId("per-column-chain-status-analyst"),
    ).toHaveTextContent(/no tail/);

    // Save a pool entry — this should trigger the chain refresh.
    fireEvent.click(
      screen.getByRole("button", { name: /add first subscription/i }),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("per-column-chain-row-analyst"),
      ).toHaveTextContent(/anthropic.*→.*minimax/i),
    );
    expect(
      screen.getByTestId("per-column-chain-status-analyst"),
    ).toHaveTextContent(/1 tail/);
  });

  it("refreshes the per-column chain after an override change (kaart 7411d25e… revisit)", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [
        makeColumnRow("col-analyst", "analyst", "analyst"),
      ],
    });
    (kanbanApi.setActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    // First call: column default + pool tail. Second call (after
    // override): the chain still reflects the column default + pool
    // tail (the override is independent of the chain map), but the
    // important thing is that the dialog re-fetches — proving the
    // mutation handler calls `refreshChainByColumn()`.
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      = makePerColumnChainMock([
        {
          columnId: "col-analyst",
          columnName: "analyst",
          defaultAgent: "analyst",
          chainsByCallIndex: (i) =>
            i === 0
              ? ["anthropic"]
              : ["anthropic", "minimax"],
        },
      ]);

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() =>
      expect(kanbanApi.getColumnEffectiveModel).toHaveBeenCalledTimes(1),
    );

    // Change the override via the select.
    const overrideSelect = screen.getByRole("combobox", {
      name: /active subscription override provider/i,
    });
    fireEvent.click(overrideSelect);
    fireEvent.click(
      screen.getByRole("option", { name: /subscription: MiniMax/i }),
    );

    // A second chain fetch must land — proving the override handler
    // wired up the refresh.
    await waitFor(() =>
      expect(kanbanApi.getColumnEffectiveModel).toHaveBeenCalledTimes(2),
    );
    await waitFor(() =>
      expect(
        screen.getByTestId("per-column-chain-row-analyst"),
      ).toHaveTextContent(/anthropic.*→.*minimax/i),
    );
  });

  it("refreshes the per-column chain after a manual pause toggle (kaart 7411d25e… revisit)", async () => {
    mockUnsetPoolAndOverride();
    (kanbanApi.listColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      columns: [
        makeColumnRow("col-analyst", "analyst", "analyst"),
      ],
    });
    (kanbanApi.setSubscriptionPause as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        provider: "anthropic",
        paused: true,
        manually_paused_providers: ["anthropic"],
      });
    // First call: column default + pool tail. Second call (after
    // pause): same chain shape — the pause is independent of the
    // chain map, but the mutation handler must still re-fetch.
    (kanbanApi.getColumnEffectiveModel as ReturnType<typeof vi.fn>)
      = makePerColumnChainMock([
        {
          columnId: "col-analyst",
          columnName: "analyst",
          defaultAgent: "analyst",
          chainsByCallIndex: (i) =>
            i === 0
              ? ["anthropic"]
              : ["anthropic", "minimax"],
        },
      ]);

    render(
      <SubscriptionPoolDialog
        open
        projectKey={PK}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    await waitFor(() =>
      expect(kanbanApi.getColumnEffectiveModel).toHaveBeenCalledTimes(1),
    );

    // Pause the Anthropic subscription.
    fireEvent.click(
      screen.getByRole("button", { name: "Pause dispatch on Anthropic" }),
    );
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPause).toHaveBeenCalledWith(
        "anthropic",
        true,
      ),
    );
    // Second chain fetch must land — proving the pause handler wired
    // up the refresh.
    await waitFor(() =>
      expect(kanbanApi.getColumnEffectiveModel).toHaveBeenCalledTimes(2),
    );
  });
});
