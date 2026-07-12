// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { PortfolioOverview } from "./types";

const overview: PortfolioOverview = {
  projects: [
    {
      id: 1,
      name: "Alpha Meta",
      kind: "meta",
      project_key: "alpha-meta",
      autodispatch_enabled: true,
      totals: { backlog: 1, todo: 0, doing: 0, impediment: 0, done_24h: 0 },
      last_activity: null,
      last_dispatch: null,
    },
    {
      id: 2,
      name: "Bravo Product",
      kind: "product",
      project_key: "bravo-product",
      autodispatch_enabled: false,
      totals: { backlog: 2, todo: 1, doing: 0, impediment: 0, done_24h: 0 },
      last_activity: null,
      last_dispatch: null,
    },
    {
      id: 3,
      name: "Charlie Archived",
      kind: "archived",
      project_key: "charlie-archived",
      autodispatch_enabled: false,
      totals: { backlog: 0, todo: 0, doing: 0, impediment: 0, done_24h: 0 },
      last_activity: null,
      last_dispatch: null,
    },
  ],
  totals: { backlog: 3, todo: 1, doing: 0, impediment: 0, done_24h: 0 },
};

vi.mock("./api", () => ({
  fetchPortfolioOverview: vi.fn(async () => overview),
}));

const { PortfolioPage } = await import("./PortfolioPage");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("PortfolioPage kind filter", () => {
  it("shows every project under the default 'All' filter", async () => {
    render(<PortfolioPage />);
    await waitFor(() => expect(screen.getByText("Alpha Meta")).toBeTruthy());
    expect(screen.getByText("Bravo Product")).toBeTruthy();
    expect(screen.getByText("Charlie Archived")).toBeTruthy();
  });

  it("narrows to a single kind when a filter chip is selected", async () => {
    render(<PortfolioPage />);
    await waitFor(() => expect(screen.getByText("Alpha Meta")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /meta only/i }));
    expect(screen.getByText("Alpha Meta")).toBeTruthy();
    expect(screen.queryByText("Bravo Product")).toBeNull();
    expect(screen.queryByText("Charlie Archived")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /product only/i }));
    expect(screen.queryByText("Alpha Meta")).toBeNull();
    expect(screen.getByText("Bravo Product")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /archived/i }));
    expect(screen.getByText("Charlie Archived")).toBeTruthy();
    expect(screen.queryByText("Alpha Meta")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /^all$/i }));
    expect(screen.getByText("Alpha Meta")).toBeTruthy();
    expect(screen.getByText("Bravo Product")).toBeTruthy();
    expect(screen.getByText("Charlie Archived")).toBeTruthy();
  });

  it("keeps the aggregate header at the portfolio-wide total regardless of filter", async () => {
    render(<PortfolioPage />);
    await waitFor(() => expect(screen.getByText("Alpha Meta")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /meta only/i }));
    // The backlog aggregate tile still reads 3 (all projects); the filtered
    // meta-only table row has a backlog of 1, so a lone "3" can only be the header.
    expect(screen.getByText("3")).toBeTruthy();
  });
});
