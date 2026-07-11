// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api", () => ({
  kanbanApi: {
    dispatchPause: vi.fn(),
    clearDispatchPause: vi.fn(),
  },
}));

const { kanbanApi } = await import("../api");
const { toast } = await import("sonner");
const { DispatchPauseBanner } = await import("./DispatchPauseBanner");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("DispatchPauseBanner", () => {
  it("renders nothing when auto-dispatch is not paused", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
    });
    const { container } = render(<DispatchPauseBanner />);
    await waitFor(() => expect(kanbanApi.dispatchPause).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows a resume button only when paused, and clears the pause on click", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({ paused: true, paused_until: null })
      .mockResolvedValueOnce({ paused: false, paused_until: null });
    (kanbanApi.clearDispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      cleared: true,
      was_paused: true,
    });

    render(<DispatchPauseBanner />);

    const button = await screen.findByRole("button", { name: /resume auto-dispatch now/i });
    fireEvent.click(button);

    await waitFor(() => expect(kanbanApi.clearDispatchPause).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
    expect(kanbanApi.dispatchPause).toHaveBeenCalledTimes(2);
  });

  it("shows an error toast when the clear call reports nothing was paused", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: true,
      paused_until: null,
    });
    (kanbanApi.clearDispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      cleared: false,
      was_paused: false,
    });

    render(<DispatchPauseBanner />);

    const button = await screen.findByRole("button", { name: /resume auto-dispatch now/i });
    fireEvent.click(button);

    await waitFor(() => expect(toast.error).toHaveBeenCalledTimes(1));
  });
});
