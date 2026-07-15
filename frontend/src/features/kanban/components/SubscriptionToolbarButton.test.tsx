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
const { SubscriptionToolbarButton } = await import("./SubscriptionToolbarButton");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PK = "git:example.com/me/repo";

async function flushLoads() {
  // Both endpoints fire on mount; wait for both to have been called.
  await waitFor(() => {
    expect(kanbanApi.getSubscriptionPool).toHaveBeenCalledWith(PK);
    expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalledWith(PK);
  });
}

describe("SubscriptionToolbarButton", () => {
  it("renders nothing when projectKey is empty", () => {
    const { container } = render(<SubscriptionToolbarButton projectKey="" />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the default 'Subscriptions' label when no pool or override is set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    expect(screen.getByRole("button", { name: /subscriptions/i })).toBeInTheDocument();
  });

  it("shows 'Pool (N)' label when only a pool is set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 },
          { cli: "claude-code", provider: "minimax", model: "MiniMax-M3[1m]", drempel: 0.95 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    expect(screen.getByRole("button", { name: /pool \(2\)/i })).toBeInTheDocument();
  });

  it("shows 'Pool (1)' for a single-entry pool", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    expect(screen.getByRole("button", { name: /pool \(1\)/i })).toBeInTheDocument();
  });

  it("shows 'Pinned: <provider>' label when only an override is set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    expect(screen.getByRole("button", { name: /pinned: minimax/i })).toBeInTheDocument();
  });

  it("shows 'Pinned' label (override wins) when both pool and override are set", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 },
        ],
      });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "anthropic", model: null },
      });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    // Override wins in the precedence chain, so the toolbar advertises the
    // override state — users who care about the pool can still open the
    // dialog and see it (greyed out under the override rule).
    expect(screen.getByRole("button", { name: /pinned: anthropic/i })).toBeInTheDocument();
  });

  it("clicking the toolbar button opens the dialog", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<SubscriptionToolbarButton projectKey={PK} />);
    await flushLoads();
    fireEvent.click(screen.getByRole("button", { name: /subscriptions/i }));
    await waitFor(() =>
      expect(screen.getByRole("dialog")).toBeInTheDocument()
    );
  });
});
