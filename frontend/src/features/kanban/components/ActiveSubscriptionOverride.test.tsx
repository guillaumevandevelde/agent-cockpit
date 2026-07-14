// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api", () => ({
  kanbanApi: {
    getActiveSubscriptionOverride: vi.fn(),
    setActiveSubscriptionOverride: vi.fn(),
  },
}));

const { kanbanApi } = await import("../api");
const { toast } = await import("sonner");
const { ActiveSubscriptionOverride } = await import("./ActiveSubscriptionOverride");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const PK = "git:example.com/me/repo";

describe("ActiveSubscriptionOverride", () => {
  it("renders nothing when projectKey is empty (matches sibling-toggles pattern)", () => {
    const { container } = render(
      <ActiveSubscriptionOverride projectKey="" />
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows the 'column defaults' option when no override is set", async () => {
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<ActiveSubscriptionOverride projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalledWith(PK)
    );
    await waitFor(() =>
      expect(
        screen.getByText(/subscription: column defaults/i)
      ).toBeTruthy()
    );
  });

  it("does not render a Clear button when no override is set", async () => {
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<ActiveSubscriptionOverride projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalled()
    );
    expect(screen.queryByRole("button", { name: /clear/i })).toBeNull();
  });

  it("selects the pinned provider when an override is set", async () => {
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    render(<ActiveSubscriptionOverride projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalled()
    );
    await waitFor(() =>
      expect(screen.getByText(/subscription: minimax/i)).toBeTruthy()
    );
    expect(
      screen.getByRole("button", { name: /clear/i })
    ).toBeTruthy();
  });

  it("clicking Clear posts a null override and toggles state back to default", async () => {
    (kanbanApi.getActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        project_key: PK,
        override: { provider: "minimax", model: null },
      });
    (kanbanApi.setActiveSubscriptionOverride as ReturnType<typeof vi.fn>)
      .mockResolvedValue({ project_key: PK, override: null });
    render(<ActiveSubscriptionOverride projectKey={PK} />);
    await waitFor(() =>
      expect(kanbanApi.getActiveSubscriptionOverride).toHaveBeenCalled()
    );
    const clearBtn = await screen.findByRole("button", { name: /clear/i });
    fireEvent.click(clearBtn);
    await waitFor(() =>
      expect(kanbanApi.setActiveSubscriptionOverride).toHaveBeenCalledWith(
        PK,
        null
      )
    );
    expect(toast.success).toHaveBeenCalledWith(
      expect.stringMatching(/cleared/i)
    );
  });
});
