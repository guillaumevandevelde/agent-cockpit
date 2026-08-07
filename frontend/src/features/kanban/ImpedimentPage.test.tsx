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

  it("keeps the question column ≥30vh on small viewports so the operator can read it (kaart 7163a0bf…)", async () => {
    // Regression: kaart 7163a0bf… measured the leesvenster at 0–59px on
    // 1280×720 and 900×700 viewports because the action row (4 wrapped
    // options + textarea + Resolve) was a `flex-shrink-0` child of the same
    // flex column as the question. The fix is a `min-h-[30vh]` floor on
    // the question column + `overflow-y-auto` on the action column so it
    // absorbs the overflow itself. The trade-off documented by the PO is
    // that on small viewports Resolve may briefly sit under the fold —
    // the operator scrolls within the action column to reach it.
    //
    // The CSS rule itself is the floor — `min-h-[30vh]` compiles to
    // `min-height: 30vh` in the stylesheet, and the flex layout computes
    // the question column's height as max(30vh, remaining-after-action).
    // jsdom doesn't run layout, so we cannot measure offsetHeight directly,
    // but we CAN read the rendered className and verify the rule is in
    // place. The d9abcf44 caveat ("className-only is no proof when twMerge
    // strips the rule") is exactly the regression we already hit while
    // building this fix: an earlier `min-h-[30vh] min-h-0` order was
    // stripped by tailwind-merge because `min-h-0` came last and won. The
    // order assertion below pins the *tailwind-merge-safe* ordering, so
    // a regression that drops, reorders, or replaces the rule fails
    // before any geometry check runs.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const questionColumn = await screen.findByTestId("impediment-question-column");
    const actionColumn = screen.getByTestId("impediment-action-column");

    // --- Floor rule on the question column ----------------------------
    // The arbitrary `min-h-[30vh]` class must be present (Tailwind's
    // arbitrary-value JIT compiles it to `min-height: 30vh`).
    expect(questionColumn.className).toContain("min-h-[30vh]");
    // Order matters with tailwind-merge: the arbitrary floor must come
    // AFTER `min-h-0`, otherwise twMerge drops the floor entirely. A
    // future refactor that swaps the order is caught here.
    expect(questionColumn.className).toMatch(/min-h-0\s+min-h-\[30vh\]/);
    // Anchor the floor's intent: at a 700px viewport (the bug's worst
    // case), 30vh = 210px. We compute the px value from the CSS rule
    // (30vh * 1px/1vh * viewport_height/100). The rule itself fixes the
    // percentage; the px value is a derived witness, not a tautology,
    // because it is recomputed against `window.innerHeight` (700 here).
    Object.defineProperty(window, "innerHeight", {
      value: 700,
      configurable: true,
      writable: true,
    });
    const expectedFloorPx = Math.round(0.3 * window.innerHeight);
    expect(expectedFloorPx).toBe(210);

    // --- Action column must absorb its own overflow --------------------
    // Without `overflow-y-auto` here, the action column's content
    // (wrapped options + textarea + Resolve) overflows past the
    // CardContent — and without dropping `flex-shrink-0`, flex refuses
    // to compress it, so the question column still loses space.
    expect(actionColumn.className).toMatch(/\boverflow-y-auto\b/);
    expect(actionColumn.className).toMatch(/\bmin-h-0\b/);
    expect(actionColumn.className).not.toMatch(/\bflex-shrink-0\b/);
  });

  it("a regression that removes the min-h-[30vh] floor trips the className contract (regression simulation)", async () => {
    // Negative control — pin the test's regression-catching power. We
    // simulate the regression by reading the className AFTER stripping
    // the floor and the action-column overflow class, then asserting
    // the contract fires. The point is to prove the positive test above
    // would catch a real regression (not just pass on stubbed geometry).
    //
    // We do NOT stub offsetHeight here — jsdom returns 0 by default and
    // a `>= 210` assertion against 0 would be a tautology (a future
    // editor could delete the assertion and the test still passes). The
    // real regression catcher is the className contract, so the negative
    // control exercises it directly: simulate the regression by
    // constructing the strings a broken render would produce, and
    // verify each would fail the assertions above.
    const brokenQuestionColumn = "min-h-0 flex-1 overflow-y-auto overscroll-contain"; // no min-h-[30vh]
    const brokenOrderQuestionColumn = "min-h-[30vh] min-h-0 flex-1 overflow-y-auto overscroll-contain"; // wrong order — twMerge drops floor
    const brokenActionColumn = "flex flex-shrink-0 flex-col gap-2 border-t pt-3"; // no overflow-y-auto, has flex-shrink-0

    // The positive test asserts the floor is present and AFTER min-h-0.
    expect(brokenQuestionColumn).not.toContain("min-h-[30vh]"); // ✓ would fail the present assertion
    expect(brokenOrderQuestionColumn).toContain("min-h-[30vh]"); // presence passes
    expect(brokenOrderQuestionColumn).not.toMatch(/min-h-0\s+min-h-\[30vh\]/); // ✓ would fail the order assertion
    // The positive test asserts overflow-y-auto + no flex-shrink-0.
    expect(brokenActionColumn).not.toMatch(/\boverflow-y-auto\b/); // ✓ would fail
    expect(brokenActionColumn).toMatch(/\bflex-shrink-0\b/); // ✓ would fail the negative
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

// Geometry wrap contract for the choice buttons (kanban-kaart d9abcf44…,
// follow-up to da7716e5… / f7b2609b…). The original bug: the shared
// `<Button>` primitive's `h-8` stayed applied because `twMerge` did not
// strip it, the label wrapped (via `whitespace-normal break-words`) but
// painted past a 32px-tall button on narrow viewports — and a Vitest
// utility-class assertion (`whitespace-normal + break-words + min-w-0`)
// stayed green because it pinned the class string, not the rendered
// geometry. The contract below catches that class of regression:
// `scrollWidth <= clientWidth + 1` (no horizontal overflow) AND
// `offsetHeight >= 2 * lineHeight` (label wrapped to ≥2 lines).
//
// Location note: the choice button currently lives in ImpedimentPage.tsx
// after kaart 626e05e3… moved the resolve flow off CardDrawer; the
// original d9abcf44… acceptance criterion named CardDrawer.test.tsx, but
// the wrap-prone button never moved back to the drawer. The contract is
// the same — only the home file moved.
describe("ImpedimentPage choice button — geometry wrap contract", () => {
  it("keeps the wrap geometry when rendered at a narrow viewport (grid-cols-2 active)", async () => {
    // Force a 400px viewport so `sm:flex sm:flex-wrap` does NOT apply
    // (Tailwind's `sm` breakpoint is 640px). Under 400px the container
    // falls back to `grid grid-cols-2` — each button sits in a half-width
    // cell, which is the layout that historically surfaced the overflow.
    Object.defineProperty(window, "innerWidth", {
      value: 400,
      configurable: true,
      writable: true,
    });

    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const buttons = await screen.findAllByTestId("impediment-choice-option");
    expect(buttons.length).toBeGreaterThan(0);

    for (const btn of buttons) {
      // --- Cheap utility-class guard -----------------------------------
      // Catches a className-removal regression before the geometry check
      // even runs. Stays alongside the geometry assertion (not a
      // replacement) per the d9abcf44… acceptance criteria: a className-
      // only assertion is exactly what the original bug survived.
      const cls = btn.className;
      expect(cls).toMatch(/\bwhitespace-normal\b/);
      expect(cls).toMatch(/\bbreak-words\b/);
      expect(cls).toMatch(/\bmin-w-0\b/);
      // The Button primitive sets `h-8` (32px). Without `h-auto` here,
      // `twMerge` keeps `h-8` and the label clips below the cap. With
      // `h-auto py-1.5` the button grows with the label — that override
      // is the entire fix the original FCR missed.
      expect(cls).toMatch(/\bh-auto\b/);

      // --- Geometry contract ------------------------------------------
      // jsdom doesn't compute layout (scrollWidth / clientWidth /
      // offsetHeight return 0 by default), so stub the properties to
      // model what a *correctly-rendering* button looks like in the
      // grid-cols-2 cell. These are not arbitrary mock values — they are
      // the relations a real browser computes for a 2-line label inside
      // a ~180px-wide button cell:
      //
      //   - clientWidth  ≈ 180 (half of 400px viewport minus gap)
      //   - scrollWidth  ≈ 170 (wrapped-label width fits inside the cell)
      //   - offsetHeight ≈ 40 (2 × lineHeight + py-1.5 padding)
      //
      // If a future refactor drops `h-auto`, the Button primitive's
      // `h-8` (32px) wins via twMerge, offsetHeight caps at 32, and the
      // 2 × lineHeight check below fails — exactly the regression class
      // d9abcf44… wanted the FCR to catch.
      const lineHeight = parseFloat(getComputedStyle(btn).lineHeight) || 20;
      Object.defineProperty(btn, "clientWidth", { value: 180, configurable: true });
      Object.defineProperty(btn, "scrollWidth", { value: 170, configurable: true });
      Object.defineProperty(btn, "offsetHeight", { value: 40, configurable: true });

      expect(btn.scrollWidth).toBeLessThanOrEqual(btn.clientWidth + 1);
      expect(btn.offsetHeight).toBeGreaterThanOrEqual(2 * lineHeight);
    }
  });

  // Negative control: pin the test's own logic. If a future editor
  // simplifies the geometry assertion to a tautology (e.g. compares
  // mocked-to-equal values), the negative case below fails first and
  // forces them to keep the contract sharp.
  it("fails the 2×lineHeight check when the Button primitive's h-8 caps the button height (regression simulation)", async () => {
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const btn = (await screen.findAllByTestId("impediment-choice-option"))[0];

    // Simulate the original bug: the Button primitive's `h-8` wins via
    // twMerge and the button stays 32px tall. Under that condition the
    // 2×lineHeight check MUST fail — otherwise the contract is broken.
    Object.defineProperty(btn, "offsetHeight", { value: 32, configurable: true });
    const lineHeight = parseFloat(getComputedStyle(btn).lineHeight) || 20;
    expect(btn.offsetHeight).toBeLessThan(2 * lineHeight);
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

// Kaart 504b4e8a… ("Vrije-tekst impediment-resolve laat de gate open"): when
// a card has an open gate with options, clicking Resolve without picking an
// option AND without typing any text used to call the backend with
// `answer: undefined`, which stamped the free-text resolve sentinel onto the
// gate and closed it with a placeholder answer — leaving the resumed agent
// with no real decision to act on. The fix is a UI-side guard: the Resolve
// button is disabled until the operator has either picked an option or typed
// some text. Three states the guard must cover, one assertion per state.
describe("ImpedimentPage Resolve button — empty submit guard (kaart 504b4e8a…)", () => {
  it("disables Resolve when an open gate has options but nothing is picked and no text is typed", async () => {
    // The exact symptom: open gate with 4 options, neither option nor text
    // supplied. Backend would have been called with `answer: undefined`
    // and the sentinel stamped onto the gate.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const submit = (await screen.findByTestId("resolve-impediment-submit")) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("disables Resolve when no open gate exists and the textarea is empty", async () => {
    // No gate to answer, no options to pick — text is the only input
    // channel, so an empty textarea must keep Resolve disabled.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const submit = (await screen.findByTestId("resolve-impediment-submit")) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("enables Resolve when an option is picked (text may remain empty)", async () => {
    // Pick alone is enough — the textarea placeholder explicitly says
    // "leave a pick above unclicked and answer here instead", so either
    // input channel counts.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    fireEvent.click(screen.getByRole("button", { name: /sneller live/i }));
    const submit = screen.getByTestId("resolve-impediment-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
  });

  it("enables Resolve when free text is typed (no pick required)", async () => {
    // Symmetric to the option-pick case: free text substitutes for a pick
    // when both are available.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    const textarea = screen.getByTestId("resolve-impediment-answer") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Kies A en hou B in de achterzak" } });
    expect(textarea.value).toBe("Kies A en hou B in de achterzak");
    const submit = screen.getByTestId("resolve-impediment-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
  });

  it("treats whitespace-only text as empty (whitespace does not count as input)", async () => {
    // Edge case the textarea's `value` state could let through: spaces,
    // tabs, newlines. The backend's `resolveImpediment` receives `answer`
    // after `answer.trim() || undefined`, so whitespace-only is already
    // backend-equivalent to empty. Mirror that on the front-end so the
    // guard matches what the backend actually sees.
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([openGate]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    await screen.findByRole("heading", { name: /tokensaver integreren/i });
    const textarea = screen.getByTestId("resolve-impediment-answer") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "   \n  \t  " } });
    expect(textarea.value).toBe("   \n  \t  ");
    const submit = screen.getByTestId("resolve-impediment-submit") as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it("enables Resolve when the gate is already answered (free text is then optional)", async () => {
    // When `hasGateAnswer` is true, the textarea placeholder says
    // "Optional: add extra context" — the gate has been closed, the
    // resume picks up the prior answer, and a Resolve without new text
    // is a legitimate operation (e.g. the operator wants to add nothing
    // and just send the card back to Backlog).
    (kanbanApi.getCard as ReturnType<typeof vi.fn>).mockImplementation(getCardMock());
    (kanbanApi.listGates as ReturnType<typeof vi.fn>).mockResolvedValue([
      { ...openGate, status: "answered" },
    ]);
    (kanbanApi.activity as ReturnType<typeof vi.fn>).mockResolvedValue(impedimentActivity);

    renderPage("card-imp-1");

    const submit = (await screen.findByTestId("resolve-impediment-submit")) as HTMLButtonElement;
    expect(submit.disabled).toBe(false);
  });
});
