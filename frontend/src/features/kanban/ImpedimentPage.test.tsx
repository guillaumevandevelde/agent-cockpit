// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { Card, Gate } from "./types";

const navigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return {
    ...actual,
    useNavigate: () => navigate,
  };
});

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/contexts/ProjectContext", () => ({
  useProjectContext: () => ({
    activeProject: {
      path: "/tmp/test-project",
      id: "1",
      name: "test-project",
      is_active: true,
    },
  }),
}));

vi.mock("./api", async (importOriginal) => {
  const actual = (await importOriginal()) as { kanbanApi: Record<string, unknown> };
  const stub: Record<string, ReturnType<typeof vi.fn>> = {};
  for (const key of Object.keys(actual.kanbanApi)) {
    stub[key] = vi.fn(async () => ({}));
  }
  return { kanbanApi: stub };
});

const { kanbanApi } = await import("./api");
const { ImpedimentPage } = await import("./ImpedimentPage");

const cardInImpediment: Card = {
  id: "card-imp-1",
  project_key: "proj-1",
  title: "Tokensaver integreren",
  description: "",
  column: "Impediment",
  rank: "0001",
  work_type: "feature",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  deliverables: [],
};

const cardNotInImpediment: Card = {
  ...cardInImpediment,
  id: "card-backlog-1",
  column: "Backlog",
};

const openGate: Gate = {
  id: "gate-1",
  card_id: "card-imp-1",
  project_key: "proj-1",
  question: "Welke richting kies je?",
  options: [
    "A: sneller live, meer onderhoud later",
    "B: trager live, minder onderhoud",
    "C: helemaal uitstellen",
    "D: expert inhuren",
  ],
  status: "open",
  created_at: "2026-01-01T00:00:00Z",
};

const impedimentActivity = [
  {
    hlc: "1",
    op_type: "comment",
    entity_type: "card",
    payload: {
      text:
        "**Impediment:** Zet een operator de token-saver aan op een lane, dan kan de agent op die lane niet meer shippen — en volgt hij de voorgeschreven herstelstap, dan staat de saver daarna board-breed aan en is de kill-switch machteloos. Precies het tegenovergestelde van de eerste kaart-eis \"Opt-in, nooit default. Niet board-breed aanzetten\".",
    },
    created_at: "2026-01-01T00:00:00Z",
  },
];

function getCardMock() {
  return vi.fn(async (id: string) => {
    if (id === "card-imp-1") return cardInImpediment;
    if (id === "card-backlog-1") return cardNotInImpediment;
    const err: Error & { status?: number } = new Error("Not found");
    err.status = 404;
    throw err;
  });
}

function renderPage(cardId: string) {
  return render(
    <MemoryRouter initialEntries={[`/kanban/impediment/${cardId}`]}>
      <Routes>
        <Route path="/kanban/impediment/:cardId" element={<ImpedimentPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  navigate.mockClear();
  (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockReset();
  (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockReset();
  (kanbanApi.answerGate as ReturnType<typeof vi.fn>).mockReset();
  (kanbanApi.resolveImpediment as ReturnType<typeof vi.fn>).mockReset();
  (kanbanApi.activity as ReturnType<typeof vi.fn>).mockReset();
});

describe("ImpedimentPage rendering", () => {
  it("renders the card title and the latest **Impediment:** question", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    // Question text — the part after **Impediment:** prefix, rendered through
    // MarkdownRenderer.
    expect(
      await screen.findByText(/token-saver aan op een lane/i),
    ).toBeTruthy();
    // The fetches were issued.
    expect(kanbanApi.activity).toHaveBeenCalledWith("card-imp-1");
    expect(kanbanApi.listGates).toHaveBeenCalledWith("card-imp-1");
  });

  it("renders the four choice buttons from the open gate", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    for (const label of openGate.options) {
      expect(screen.getByRole("button", { name: new RegExp(label, "i") })).toBeTruthy();
    }
  });

  it("renders the textarea and the Resolve button", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    expect(screen.getByTestId("resolve-impediment-answer")).toBeTruthy();
    expect(screen.getByTestId("resolve-impediment-submit")).toBeTruthy();
  });

  it("renders the action row anchored beneath the question column (option A)", async () => {
    // Option A: action surface stays anchored at the bottom (flex-shrink-0)
    // while the question column scrolls (flex-1 + overflow-y-auto). We
    // assert the structural test-ids are both present in the same render
    // and that the action column carries the dedicated test-id we ship it
    // with so a future regression cannot silently demote it to a sub-element
    // of the question column.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    const questionColumn = screen.getByTestId("impediment-question-column");
    const actionColumn = screen.getByTestId("impediment-action-column");
    expect(questionColumn).toBeTruthy();
    expect(actionColumn).toBeTruthy();
    // The action column is rendered as a sibling of the question column, not
    // nested inside it — a regression that moved it inside the scroll
    // container would re-introduce the original "scroll past the Resolve
    // button" bug.
    expect(actionColumn.contains(questionColumn)).toBe(false);
    expect(questionColumn.contains(actionColumn)).toBe(false);
  });

  it("renders the Refresh button with a working onClick handler", async () => {
    // Regression for kaart 626e05e3…: the earlier ImpedimentPage passed
    // `onRefresh` to RefreshButton, which expects `onClick`. TypeScript
    // silently dropped the wrong prop, so the button rendered with no
    // handler. Now it should re-issue the activity fetch when clicked.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });

    const refreshButton = await screen.findByRole("button", { name: /refresh/i });
    const callsBefore = (kanbanApi.activity as ReturnType<typeof vi.fn>).mock.calls.length;
    fireEvent.click(refreshButton);
    const callsAfter = (kanbanApi.activity as ReturnType<typeof vi.fn>).mock.calls.length;
    // The refresh must reach the activity fetcher — passing `onRefresh`
    // instead of `onClick` left the button without a handler, so the click
    // was a silent no-op.
    expect(callsAfter).toBeGreaterThan(callsBefore);
  });
});

describe("ImpedimentPage state guards", () => {
  it("shows a 'card no longer in Impediment' message when the card is on Backlog", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    renderPage("card-backlog-1");

    expect(
      await screen.findByText(/no longer in the impediment column/i),
    ).toBeTruthy();
    expect(screen.queryByTestId("resolve-impediment-submit")).toBeNull();
  });

  it("shows a 'not found' fallback when the card id resolves to nothing", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue([]);

    renderPage("does-not-exist");

    expect(await screen.findByText(/not found/i)).toBeTruthy();
    expect(screen.queryByTestId("resolve-impediment-submit")).toBeNull();
  });
});

describe("ImpedimentPage resolve flow", () => {
  it("calls answerGate + resolveImpediment on Resolve click and navigates back to /kanban", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);
    (kanbanApi.answerGate as ReturnType<typeof vi.fn>).mockResolvedValue(openGate);
    (kanbanApi.resolveImpediment as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...cardInImpediment,
      column: "Backlog",
    });

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });

    // Pick a structured option.
    fireEvent.click(
      screen.getByRole("button", { name: /sneller live/i }),
    );

    // Add some free-text context.
    fireEvent.change(screen.getByTestId("resolve-impediment-answer"), {
      target: { value: "Ga voor optie A en hou B in de achterzak" },
    });

    fireEvent.click(screen.getByTestId("resolve-impediment-submit"));

    await waitFor(() => {
      expect(kanbanApi.answerGate).toHaveBeenCalledWith("gate-1", expect.stringMatching(/sneller live/i));
    });
    expect(kanbanApi.resolveImpediment).toHaveBeenCalledWith(
      "card-imp-1",
      "/tmp/test-project",
      "Ga voor optie A en hou B in de achterzak",
    );
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith("/kanban");
    });
  });

  it("resolves without calling answerGate when no open gate exists", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);
    (kanbanApi.resolveImpediment as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...cardInImpediment,
      column: "Backlog",
    });

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    fireEvent.change(screen.getByTestId("resolve-impediment-answer"), {
      target: { value: "Gewoon doorgaan" },
    });
    fireEvent.click(screen.getByTestId("resolve-impediment-submit"));

    await waitFor(() => {
      expect(kanbanApi.answerGate).not.toHaveBeenCalled();
    });
    expect(kanbanApi.resolveImpediment).toHaveBeenCalledWith(
      "card-imp-1",
      "/tmp/test-project",
      "Gewoon doorgaan",
    );
    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith("/kanban");
    });
  });
});
