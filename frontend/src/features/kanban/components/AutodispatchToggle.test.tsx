// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { kanbanApi } from "../api";
import { AutodispatchToggle } from "./AutodispatchToggle";

vi.mock("../api", () => ({
  kanbanApi: {
    getAutodispatch: vi.fn(),
    setAutodispatch: vi.fn(),
  },
}));

const getMock = kanbanApi.getAutodispatch as ReturnType<typeof vi.fn>;
const setMock = kanbanApi.setAutodispatch as ReturnType<typeof vi.fn>;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AutodispatchToggle", () => {
  it("renders nothing while loading", () => {
    getMock.mockReturnValue(new Promise(() => {}));
    const { container } = render(<AutodispatchToggle projectKey="proj-1" />);
    expect(container.firstChild).toBeNull();
  });

  it("renders 'off' with no hint when the backend reports no marker", async () => {
    getMock.mockResolvedValue({ enabled: false });
    render(<AutodispatchToggle projectKey="proj-1" />);
    const button = await waitFor(() => screen.getByTestId("autodispatch-toggle"));
    expect(button.textContent).toMatch(/off/i);
    expect(screen.queryByTestId("autodispatch-boot-hint")).toBeNull();
  });

  it("surfaces the boot-disabled hint when the marker is present", async () => {
    // 2026-08-07T08:45:00Z — fixed so the relative-time string is deterministic
    // (anything more than a few minutes ago is fine for the assertion).
    getMock.mockResolvedValue({
      enabled: false,
      disabled_by_boot_at: "2026-08-07T08:45:00Z",
      disabled_by_boot_reason: "real_backend_start",
    });
    render(<AutodispatchToggle projectKey="proj-1" />);
    const hint = await waitFor(() =>
      screen.getByTestId("autodispatch-boot-hint"),
    );
    expect(hint.textContent).toMatch(/backend start/i);
    expect(hint.textContent).toMatch(/click to resume/i);
  });

  it("does NOT surface the hint when the flag is on (operator opted back in)", async () => {
    // The backend clears the marker when the operator enables, so this
    // response shape wouldn't normally happen — but a defensive render is
    // cheap insurance against a transient state during the round-trip.
    getMock.mockResolvedValue({
      enabled: true,
      disabled_by_boot_at: "2026-08-07T08:45:00Z",
    });
    render(<AutodispatchToggle projectKey="proj-1" />);
    const button = await waitFor(() => screen.getByTestId("autodispatch-toggle"));
    expect(button.textContent).toMatch(/on/i);
    expect(screen.queryByTestId("autodispatch-boot-hint")).toBeNull();
  });

  it("flips to enabled and clears the hint on click", async () => {
    getMock.mockResolvedValue({
      enabled: false,
      disabled_by_boot_at: "2026-08-07T08:45:00Z",
    });
    setMock.mockResolvedValue({ enabled: true });
    render(<AutodispatchToggle projectKey="proj-1" />);
    await waitFor(() => screen.getByTestId("autodispatch-boot-hint"));
    fireEvent.click(screen.getByTestId("autodispatch-toggle"));
    await waitFor(() => {
      const btn = screen.getByTestId("autodispatch-toggle");
      expect(btn.textContent).toMatch(/on/i);
    });
    expect(setMock).toHaveBeenCalledWith("proj-1", true);
    expect(screen.queryByTestId("autodispatch-boot-hint")).toBeNull();
  });
});