// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { MarkdownRenderer } from "./MarkdownRenderer";

afterEach(() => {
  cleanup();
});

function LocationDisplay() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function renderAt(content: string, initialEntry = "/kanban") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <LocationDisplay />
      <Routes>
        <Route path="/kanban" element={<MarkdownRenderer content={content} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("MarkdownRenderer link handling", () => {
  it("navigates via react-router (no full reload) for a same-origin path link", () => {
    renderAt("[Some card](/kanban?card=abc123)");

    const link = screen.getByRole("link", { name: "Some card" });
    fireEvent.click(link);

    expect(screen.getByTestId("location").textContent).toBe("/kanban?card=abc123");
  });

  it("does not mark an internal link as external (no target=_blank)", () => {
    renderAt("[Some card](/kanban?card=abc123)");
    const link = screen.getByRole("link", { name: "Some card" });
    expect(link.getAttribute("target")).toBeNull();
  });

  it("leaves an http(s) link's href untouched and opens it in a new tab", () => {
    renderAt("[External](https://example.com/foo)");
    const link = screen.getByRole("link", { name: "External" });
    expect(link.getAttribute("href")).toBe("https://example.com/foo");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toMatch(/noopener/);
  });

  it("leaves a mailto: link's href untouched and opens it in a new tab", () => {
    renderAt("[Mail me](mailto:foo@example.com)");
    const link = screen.getByRole("link", { name: "Mail me" });
    expect(link.getAttribute("href")).toBe("mailto:foo@example.com");
    expect(link.getAttribute("target")).toBe("_blank");
  });
});
