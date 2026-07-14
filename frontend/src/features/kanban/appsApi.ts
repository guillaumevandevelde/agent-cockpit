import { apiClient } from "@/lib/api";
import type { RunInstance } from "./types";

const BASE = "runs/app";

export interface StartRunRequest {
  project_path: string;
  command: string[];
  env?: Record<string, string>;
  port?: number;
  health_path?: string;
  health_timeout_s?: number;
}

// Wraps ``backend/app/api/v1/app_runs/router.py`` — the wire path is
// ``/api/v1/runs/app`` (router prefix ``/runs`` + path ``/app``). Reused by the
// Done-card "Run this branch" preview control (kanban-card d2689f2d).
export const appsApi = {
  startRun: (req: StartRunRequest): Promise<RunInstance> =>
    apiClient<RunInstance>(BASE, {
      method: "POST",
      body: JSON.stringify(req),
    }),

  getRun: (instanceId: string): Promise<RunInstance> =>
    apiClient<RunInstance>(`${BASE}/${encodeURIComponent(instanceId)}`),

  listRuns: (projectPath: string): Promise<{ runs: RunInstance[] }> =>
    apiClient<{ runs: RunInstance[] }>(
      `${BASE}?project_path=${encodeURIComponent(projectPath)}`,
    ),

  stopRun: (instanceId: string): Promise<{ success: boolean; instance_id: string }> =>
    apiClient<{ success: boolean; instance_id: string }>(
      `${BASE}/${encodeURIComponent(instanceId)}`,
      { method: "DELETE" },
    ),
};