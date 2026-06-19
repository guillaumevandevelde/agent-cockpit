// APM (Agent Package Manager) TypeScript types

export interface ApmStatus {
  apm_installed: boolean;
  apm_binary_path: string | null;
  apm_yml_exists: boolean;
  apm_yml_path: string;
  apm_lock_exists: boolean;
  apm_lock_path: string;
  project_path: string;
}

export interface ApmDependency {
  name: string;
  source: string;
  version?: string;
}

export interface ApmDependenciesResponse {
  exists: boolean;
  name?: string;
  version?: string;
  dependencies: Record<string, unknown>;
  dev_dependencies: Record<string, unknown>;
  project_path: string | null;
}

export interface ApmDependencyAddRequest {
  name: string;
  source: string;
  is_dev?: boolean;
}

export interface ApmInstallRequest {
  frozen?: boolean;
}

export interface ApmInstallResponse {
  success: boolean;
  exit_code?: number;
  stdout?: string;
  stderr?: string;
  message?: string;
}

export interface ApmSyncRequest {
  source_project: string;
  target_project: string;
}

export interface ApmSyncResponse {
  success: boolean;
  message: string;
  added: unknown[];
  total_target_deps: number;
}

export interface ApmModule {
  name: string;
  path: string;
}

export interface ApmModulesResponse {
  exists: boolean;
  modules: ApmModule[];
  path: string;
}
