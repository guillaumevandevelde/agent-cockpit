/** Sandcastle configuration for a project */
export interface SandcastleConfig {
  id: number;
  project_path: string;
  enabled: boolean;
  sandbox_provider: 'docker' | 'podman' | 'vercel' | 'no-sandbox';
  agent_provider: 'claude-code' | 'codex' | 'cursor' | 'pi' | 'opencode' | 'copilot';
  model: string | null;
  branch_strategy: 'head' | 'merge-to-head' | 'branch';
  docker_image: string | null;
  max_iterations: number;
  idle_timeout_seconds: number;
  permission_mode: string;
  created_at: string | null;
  updated_at: string | null;
}

/** Request to update sandcastle config */
export interface SandcastleConfigUpdate {
  enabled?: boolean;
  sandbox_provider?: string;
  agent_provider?: string;
  model?: string | null;
  branch_strategy?: string;
  docker_image?: string | null;
  max_iterations?: number;
  idle_timeout_seconds?: number;
  permission_mode?: string;
}

/** Sandcastle run record */
export interface SandcastleRun {
  id: number;
  project_path: string;
  prompt: string;
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
  branch: string | null;
  commits: { sha: string }[] | null;
  stdout: string | null;
  stderr: string | null;
  error: string | null;
  pid: number | null;
  log_file_path: string | null;
  output: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string | null;
}

/** Request to start a sandcastle run */
export interface SandcastleRunRequest {
  prompt: string;
  config_id?: number;
  branch_name?: string;
  max_iterations?: number;
}

/** Health check response */
export interface SandcastleHealth {
  node_available: boolean;
  docker_available: boolean;
  podman_available: boolean;
  runner_script_exists: boolean;
  docker_image_exists?: boolean;
  npm_dependencies_installed?: boolean;
  node_version?: string;
  docker_version?: string;
  podman_version?: string;
}

/** List of configs response */
export interface SandcastleConfigListResponse {
  configs: {
    id: number;
    project_path: string;
    enabled: boolean;
    sandbox_provider: string;
    agent_provider: string;
    created_at: string | null;
  }[];
}

/** List of runs response */
export interface SandcastleRunListResponse {
  runs: {
    id: number;
    project_path: string;
    prompt: string;
    status: string;
    branch: string | null;
    commits: { sha: string }[] | null;
    started_at: string | null;
    completed_at: string | null;
    created_at: string | null;
  }[];
}

/** Run statistics */
export interface SandcastleStats {
  total_runs: number;
  runs_by_status: Record<string, number>;
  recent_runs_24h: number;
  active_runs: number;
}