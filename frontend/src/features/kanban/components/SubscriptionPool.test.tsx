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
  },
}));

const { kanbanApi } = await import("../api");
const { toast } = await import("sonner");
const { SubscriptionPool } = await import("./SubscriptionPool");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PK = "git:example.com/me/repo";

describe("SubscriptionPool", () => {
  it("renders nothing when projectKey is empty", () => {
    const { container } = render(<SubscriptionPool projectKey="" />);
    expect(container.innerHTML).toBe("");
  });

  it("shows the 'no pool' empty state when API returns null", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    render(<SubscriptionPool projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalledWith(PK)
    );
    await waitFor(() =>
      expect(
        screen.getByText(/No subscription pool configured/i)
      ).toBeTruthy()
    );
  });

  it("renders an existing pool as ordered entries", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [
          { cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 },
          { cli: "claude-code", provider: "minimax", model: "MiniMax-M3[1m]", drempel: 0.95 },
        ],
      });
    render(<SubscriptionPool projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled()
    );
    // Both providers should appear in the rendered selects.
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.getByText("MiniMax")).toBeTruthy();
    // Both drempel inputs (number type=number) should be present.
    const numbers = screen.getAllByDisplayValue(/^0\.9/);
    expect(numbers.length).toBeGreaterThanOrEqual(1);
  });

  it("Add first subscription creates a default entry and POSTs it", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        pool: [{ cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 }],
      });
    render(<SubscriptionPool projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled()
    );
    const addBtn = await screen.findByRole("button", { name: /add first subscription/i });
    fireEvent.click(addBtn);
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(
        PK,
        [{ cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 }],
      )
    );
  });

  it("Clear button removes every entry by POSTing null", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        project_key: PK,
        pool: [{ cli: "claude-code", provider: "anthropic", model: null, drempel: 0.9 }],
      });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, pool: null });
    render(<SubscriptionPool projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled()
    );
    const clearBtn = await screen.findByRole("button", { name: /^clear$/i });
    fireEvent.click(clearBtn);
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalledWith(PK, null)
    );
  });

  it("saving a failed POST rolls back to the server's current value", async () => {
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ project_key: PK, pool: null });
    (kanbanApi.setSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error("422 invalid drempel"));
    // The rollback fetch returns the original (unset) state.
    (kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ project_key: PK, pool: null });
    render(<SubscriptionPool projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getSubscriptionPool).toHaveBeenCalled()
    );
    const addBtn = await screen.findByRole("button", { name: /add first subscription/i });
    fireEvent.click(addBtn);
    await waitFor(() =>
      expect(kanbanApi.setSubscriptionPool).toHaveBeenCalled()
    );
    expect(toast.error).toHaveBeenCalledWith("422 invalid drempel");
    // The rollback fetch re-asserts the state.
    await waitFor(() =>
      expect((kanbanApi.getSubscriptionPool as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(2)
    );
  });
});