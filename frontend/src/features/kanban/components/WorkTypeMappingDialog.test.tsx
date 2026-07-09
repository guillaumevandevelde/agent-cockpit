// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WorkTypeMappingDialog } from "./WorkTypeMappingDialog";

vi.mock("../api", () => {
  const listWorkTypeMappings = vi.fn(async () => ({
    project_key: "P",
    mappings: {
      analysis: "analyst",
      feature: "engineer",
      bug: "engineer",
      chore: "engineer",
    },
  }));
  const bulkPutWorkTypeMappings = vi.fn(async () => []);
  const deleteWorkTypeMapping = vi.fn(async () => undefined);
  return {
    kanbanApi: {
      listWorkTypeMappings,
      bulkPutWorkTypeMappings,
      deleteWorkTypeMapping,
    },
  };
});

const { kanbanApi } = await import("../api");
const { WORK_TYPE_PERSONA_DEFAULTS } = await import("../types");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("WorkTypeMappingDialog", () => {
  it("loads the merged mapping on open and renders one row per work_type", async () => {
    render(
      <WorkTypeMappingDialog
        open
        projectKey="P"
        onClose={() => {}}
        onChanged={() => {}}
      />
    );
    await waitFor(() =>
      expect(kanbanApi.listWorkTypeMappings).toHaveBeenCalledWith("P")
    );
    // One row per work_type: the Reset button acts as a stable per-row anchor
    // (the description also mentions "analysis", so a plain getByText would
    // match twice).
    expect(screen.getAllByRole("button", { name: "Reset" })).toHaveLength(4);
  });

  it("calls bulkPutWorkTypeMappings when Save is pressed", async () => {
    render(
      <WorkTypeMappingDialog
        open
        projectKey="P"
        onClose={() => {}}
        onChanged={() => {}}
      />
    );
    await waitFor(() =>
      expect(kanbanApi.listWorkTypeMappings).toHaveBeenCalledTimes(1)
    );
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(kanbanApi.bulkPutWorkTypeMappings).toHaveBeenCalledWith(
        "P",
        expect.arrayContaining([
          { work_type: "analysis", persona: WORK_TYPE_PERSONA_DEFAULTS.analysis },
          { work_type: "feature", persona: WORK_TYPE_PERSONA_DEFAULTS.feature },
          { work_type: "bug", persona: WORK_TYPE_PERSONA_DEFAULTS.bug },
          { work_type: "chore", persona: WORK_TYPE_PERSONA_DEFAULTS.chore },
        ])
      )
    );
  });

  it("calls deleteWorkTypeMapping when Reset is pressed for an overridden row", async () => {
    vi.mocked(kanbanApi.listWorkTypeMappings).mockResolvedValueOnce({
      project_key: "P",
      // analysis overridden to "engineer" (a non-default persona)
      mappings: {
        analysis: "engineer",
        feature: "engineer",
        bug: "engineer",
        chore: "engineer",
      },
    });
    render(
      <WorkTypeMappingDialog
        open
        projectKey="P"
        onClose={() => {}}
        onChanged={() => {}}
      />
    );
    await waitFor(() =>
      expect(kanbanApi.listWorkTypeMappings).toHaveBeenCalledTimes(1)
    );
    // Only the analysis row is overridden → exactly one enabled Reset button.
    const resetButtons = screen.getAllByRole("button", { name: "Reset" });
    const enabledResetButtons = resetButtons.filter(
      (b) => !(b as HTMLButtonElement).disabled,
    );
    expect(enabledResetButtons).toHaveLength(1);
    fireEvent.click(enabledResetButtons[0]);
    await waitFor(() =>
      expect(kanbanApi.deleteWorkTypeMapping).toHaveBeenCalledWith("P", "analysis")
    );
  });
});