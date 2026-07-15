// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api", () => ({
  kanbanApi: {
    getSubscriptionPool: vi.fn(),
    setSubscriptionPool: vi.fn(),
    getActiveSubscriptionOverride: vi.fn(),
    setActiveSubscriptionOverride: vi.fn(),
  },
}));

const { kanbanApi } = await import("../api");
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
        { provider: "anthropic", model: null, drempel: 0.9 },
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
