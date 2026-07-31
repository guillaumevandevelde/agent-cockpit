// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  return { kanbanApi: stub };
});

vi.mock("@/features/cc-bridge/TerminalView", () => ({
  TerminalView: () => <div data-testid="terminal-view" />,
}));

vi.mock("@/features/cc-bridge/api", () => ({
  fetchResumableSessions: vi.fn(async () => ({ sessions: [] })),
}));

const refreshMock = vi.fn(async () => {});
let mockSessions: { session_name: string; tmux_target: string }[] = [];
vi.mock("@/features/cc-bridge/useCCSessions", () => ({
  useCCSessions: () => ({ sessions: mockSessions, refresh: refreshMock }),
}));

vi.mock("@/hooks/useSessionsApi", () => ({
  useSessionsApi: () => ({ getSessionDetail: vi.fn(async () => ({ session: null, total_pages: 1 })) }),
}));

const { kanbanApi } = await import("../api");
const { toast } = await import("sonner");
const { CardRunTab } = await import("./CardRunTab");

afterEach(() => {
  cleanup();
  mockSessions = [];
  vi.clearAllMocks();
});

describe("CardRunTab take-over", () => {
  it("shows the Take over button when no live tmux session exists for this card", async () => {
    render(<CardRunTab cardId="card-1" sessionName="k-hl-0001" projectPath="/repo" />);
    await waitFor(() =>
      expect(screen.getByTestId("take-over-button")).not.toBeNull(),
    );
  });

  it("hides the Take over button once a live tmux session exists", () => {
    mockSessions = [{ session_name: "k-hl-0001", tmux_target: "k-hl-0001:0.0" }];
    render(<CardRunTab cardId="card-1" sessionName="k-hl-0001" projectPath="/repo" />);
    expect(screen.queryByTestId("take-over-button")).toBeNull();
  });

  it("calls kanbanApi.takeOver, refreshes sessions, and switches to Live on success", async () => {
    render(<CardRunTab cardId="card-1" sessionName="k-hl-0001" projectPath="/repo" />);
    const button = await screen.findByTestId("take-over-button");
    fireEvent.click(button);

    await waitFor(() => {
      expect(kanbanApi.takeOver).toHaveBeenCalledWith("card-1", "/repo");
    });
    expect(refreshMock).toHaveBeenCalled();
    expect(toast.success).toHaveBeenCalled();
  });

  it("shows an error toast when the take-over call fails", async () => {
    (kanbanApi.takeOver as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("no resumable transcript found for this session yet"),
    );
    render(<CardRunTab cardId="card-1" sessionName="k-hl-0001" projectPath="/repo" />);
    const button = await screen.findByTestId("take-over-button");
    fireEvent.click(button);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        "no resumable transcript found for this session yet",
      );
    });
  });
});
