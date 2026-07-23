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

  // ---- manual per-subscription pause (kaart f056b2888a…) ----------------
  //
  // Operator-toggled pause has no deadline and is independent from the
  // auto-tripped time-based slots. Banner must surface it with distinct
  // wording so the operator can tell "Claude usage-limit hit" from "I turned
  // this off myself in the Subscriptions dialog".

  it("surfaces a manually-paused provider with the distinct 'paused by you' wording", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      manually_paused_providers: ["anthropic"],
    });

    render(<DispatchPauseBanner />);

    // Distinct wording from the auto-paused / time-based message.
    // The provider label is wrapped in its own <span>, so the full sentence
    // spans multiple text nodes — match the static part that the operator
    // would actually see in the banner.
    expect(
      await screen.findByText(/dispatch paused by you/i)
    ).toBeTruthy();
    // The provider label is present in the same banner block (matched as a
    // function so the inner <span> wrapping doesn't trip the matcher).
    expect(
      await screen.findByText((_, node) => {
        if (!node) return false;
        return (
          node.textContent === "Anthropic" &&
          node.tagName.toLowerCase() === "span"
        );
      })
    ).toBeTruthy();
    // No time suffix for a manual pause (no deadline).
    expect(screen.queryByText(/paused by you.*until/i)).toBeNull();
  });

  it("treats a missing manually_paused_providers field as empty (backward-compat)", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: null,
      paused_providers: [],
      // manually_paused_providers intentionally omitted.
    });

    const { container } = render(<DispatchPauseBanner />);
    await waitFor(() => expect(kanbanApi.dispatchPause).toHaveBeenCalled());
    expect(container.innerHTML).toBe("");
  });

  it("renders manual-pause lines alongside the time-based ones when both are active", async () => {
    (kanbanApi.dispatchPause as ReturnType<typeof vi.fn>).mockResolvedValue({
      paused: false,
      paused_until: "2026-07-12T17:00:00+00:00",
      paused_providers: ["minimax"],
      manually_paused_providers: ["bedrock"],
    });

    render(<DispatchPauseBanner />);

    // Time-based line keeps the auto-paused wording.
    expect(
      await screen.findByText(/auto-dispatch paused for minimax until /i)
    ).toBeTruthy();
    // Manual line uses the operator-pause wording (text is split across
    // nested spans, so match the static suffix only).
    expect(screen.getByText(/dispatch paused by you/i)).toBeTruthy();
  });
});
