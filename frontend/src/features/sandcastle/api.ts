import { apiClient, buildEndpoint } from '@/lib/api';
import type {
  SandcastleConfig,
  SandcastleConfigUpdate,
  SandcastleRun,
  SandcastleRunRequest,
  SandcastleHealth,
  SandcastleConfigListResponse,
  SandcastleRunListResponse,
} from './types';

const BASE = 'sandcastle';

/** Get sandcastle config for a project */
export async function getSandcastleConfig(projectPath: string): Promise<SandcastleConfig> {
  return apiClient<SandcastleConfig>(
    buildEndpoint(`${BASE}/config`, { project_path: projectPath })
  );
}

/** Update sandcastle config for a project */
export async function updateSandcastleConfig(
  projectPath: string,
  updates: SandcastleConfigUpdate
): Promise<SandcastleConfig> {
  return apiClient<SandcastleConfig>(
    buildEndpoint(`${BASE}/config`, { project_path: projectPath }),
    { method: 'PUT', body: JSON.stringify(updates) }
  );
}

/** Toggle sandcastle config enabled status */
export async function toggleSandcastleConfig(configId: number): Promise<{ id: number; enabled: boolean }> {
  return apiClient<{ id: number; enabled: boolean }>(
    `${BASE}/config/${configId}/toggle`,
    { method: 'PATCH' }
  );
}

/** List all sandcastle configs */
export async function listSandcastleConfigs(): Promise<SandcastleConfigListResponse> {
  return apiClient<SandcastleConfigListResponse>(`${BASE}/configs`);
}

/** Start a new sandcastle run */
export async function startSandcastleRun(
  projectPath: string,
  request: SandcastleRunRequest
): Promise<SandcastleRun> {
  return apiClient<SandcastleRun>(
    buildEndpoint(`${BASE}/runs`, { project_path: projectPath }),
    { method: 'POST', body: JSON.stringify(request) }
  );
}

/** Start multiple sandcastle runs in parallel */
export async function startParallelSandcastleRuns(
  projectPath: string,
  prompts: { prompt: string; branch_name?: string }[],
  configId?: number,
  useSharedSandbox?: boolean
): Promise<{ runs: SandcastleRun[] }> {
  return apiClient<{ runs: SandcastleRun[] }>(
    buildEndpoint(`${BASE}/runs/parallel`, { project_path: projectPath }),
    {
      method: 'POST',
      body: JSON.stringify({
        prompts,
        config_id: configId,
        use_shared_sandbox: useSharedSandbox,
      }),
    }
  );
}

/** List sandcastle runs */
export async function listSandcastleRuns(
  projectPath?: string,
  status?: string,
  limit?: number
): Promise<SandcastleRunListResponse> {
  return apiClient<SandcastleRunListResponse>(
    buildEndpoint(`${BASE}/runs`, { project_path: projectPath, status, limit })
  );
}

/** Get a sandcastle run by ID */
export async function getSandcastleRun(runId: number): Promise<SandcastleRun> {
  return apiClient<SandcastleRun>(`${BASE}/runs/${runId}`);
}

/** Cancel a running sandcastle run (keeps the record) */
export async function cancelSandcastleRun(runId: number): Promise<{ success: boolean }> {
  return apiClient<{ success: boolean }>(
    `${BASE}/runs/${runId}/cancel`,
    { method: 'POST' }
  );
}

/** Delete a single sandcastle run record (cancels it first if still active) */
export async function deleteSandcastleRun(runId: number): Promise<{ success: boolean }> {
  return apiClient<{ success: boolean }>(
    `${BASE}/runs/${runId}`,
    { method: 'DELETE' }
  );
}

/** Bulk-delete sandcastle runs. Terminal runs only unless includeRunning is set. */
export async function clearSandcastleRuns(
  projectPath?: string,
  includeRunning = false
): Promise<{ deleted: number }> {
  return apiClient<{ deleted: number }>(
    buildEndpoint(`${BASE}/runs`, { project_path: projectPath, include_running: includeRunning }),
    { method: 'DELETE' }
  );
}

/** Check sandcastle health */
export async function checkSandcastleHealth(): Promise<SandcastleHealth> {
  return apiClient<SandcastleHealth>(`${BASE}/health`);
}

/** Build sandcastle Docker image */
export async function buildSandcastleImage(imageName: string = 'sandcastle:local'): Promise<{ success: boolean; message?: string; error?: string }> {
  return apiClient<{ success: boolean; message?: string; error?: string }>(
    `${BASE}/build-image`,
    { method: 'POST', body: JSON.stringify({ image_name: imageName }) }
  );
}

/** Get sandcastle run statistics */
export async function getSandcastleStats(): Promise<{
  total_runs: number;
  runs_by_status: Record<string, number>;
  recent_runs_24h: number;
  active_runs: number;
}> {
  return apiClient<{
    total_runs: number;
    runs_by_status: Record<string, number>;
    recent_runs_24h: number;
    active_runs: number;
  }>(`${BASE}/stats`);
}

/** Get logs for a sandcastle run */
export async function getSandcastleRunLogs(runId: number, offset: number = 0): Promise<{
  run_id: number;
  status: string;
  stdout: string;
  stderr: string;
  error: string | null;
  log_content?: string;
  log_offset?: number;
}> {
  return apiClient<{
    run_id: number;
    status: string;
    stdout: string;
    stderr: string;
    error: string | null;
    log_content?: string;
    log_offset?: number;
  }>(`${BASE}/runs/${runId}/logs?offset=${offset}`);
}

/** Create a streaming connection for run logs */
export function streamSandcastleRunLogs(
  runId: number,
  onMessage: (data: Record<string, unknown>) => void,
  onError?: (error: Event) => void
): EventSource {
  const eventSource = new EventSource(`/api/v1/sandcastle/runs/${runId}/stream`);
  
  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onMessage(data);
    } catch {
      // Ignore parse errors
    }
  };
  
  eventSource.onerror = (error) => {
    onError?.(error);
  };
  
  return eventSource;
}