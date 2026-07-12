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
  it("renders nothing when auto-dispatch is not paused and no per-provider pauses exist", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
    });
    const { container } = render(<DispatchPauseBanner />);
    await waitFor(() => expect(kanbanApi.dispatchPause).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("treats a missing paused_providers field as empty (backward-compat with older responses)", async () => {
    // Older backend versions omit the field entirely; the banner must not crash.
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
    });
    const { container } = render(<DispatchPauseBanner />);
    await waitFor(() => expect(kanbanApi.dispatchPause).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("shows the existing global-pause line + resume button when only the global pause is active", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: true,
      paused_until: null,
      paused_providers: [],
    });
    (kanbanApi.clearDispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      cleared: true,
      was_paused: true,
    });

    render(<DispatchPauseBanner />);

    expect(
      await screen.findByText(/auto-dispatch paused.*claude usage limit hit/i)
    ).toBeTruthy();
    expect(screen.queryByText(/auto-dispatch paused for /i)).toBeNull();
    const button = await screen.findByRole("button", { name: /resume auto-dispatch now/i });
    fireEvent.click(button);
    await waitFor(() => expect(kanbanApi.clearDispatchPause).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
  });

  it("renders one line per paused provider when per-provider pauses are active", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: "2026-07-12T17:00:00+00:00",
      paused_providers: ["minimax", "bedrock"],
    });

    render(<DispatchPauseBanner />);

    expect(
      await screen.findByText(/auto-dispatch paused for minimax until /i)
    ).toBeTruthy();
    expect(screen.getByText(/auto-dispatch paused for bedrock until /i)).toBeTruthy();
    // No global-pause wording — only the per-provider lines are shown.
    expect(screen.queryByText(/claude usage limit hit/i)).toBeNull();
    // Resume button still clears every pause server-side.
    expect(
      await screen.findByRole("button", { name: /resume auto-dispatch now/i })
    ).toBeTruthy();
  });

  it("shows the global-pause line plus a per-provider line when both are active", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: true,
      paused_until: "2026-07-12T17:00:00+00:00",
      paused_providers: ["minimax"],
    });

    render(<DispatchPauseBanner />);

    expect(
      await screen.findByText(/auto-dispatch paused until .*claude usage limit hit/i)
    ).toBeTruthy();
    expect(screen.getByText(/auto-dispatch paused for minimax until /i)).toBeTruthy();
  });

  it("renders per-provider lines without a time when paused_until is absent", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: ["minimax"],
    });

    render(<DispatchPauseBanner />);

    expect(
      await screen.findByText(/auto-dispatch paused for minimax\./i)
    ).toBeTruthy();
  });

  it("clears all pauses (global + per-provider) via the resume button", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce({
        paused: true,
        paused_until: "2026-07-12T17:00:00+00:00",
        paused_providers: ["minimax"],
      })
      .mockResolvedValueOnce({
        paused: false,
        paused_until: null,
        paused_providers: [],
      });
    (kanbanApi.clearDispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      cleared: true,
      was_paused: true,
    });

    render(<DispatchPauseBanner />);

    const button = await screen.findByRole("button", { name: /resume auto-dispatch now/i });
    fireEvent.click(button);

    await waitFor(() => expect(kanbanApi.clearDispatchPause).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1));
  });

  it("shows an error toast when the clear call reports nothing was paused", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: true,
      paused_until: null,
      paused_providers: [],
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
