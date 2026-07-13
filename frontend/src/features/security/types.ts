// Security profile types — mirror backend/app/models/security_profile_schemas.py.
// The risk_class / network_policy unions are the canonical taxonomy from
// docs/cockpit/risk-class-taxonomie.md. They live here verbatim so a typo
// on either side surfaces as a TS compiler error during dev/build.

export type RiskClass =
  | 'meta'
  | 'product-staging'
  | 'product-prod'
  | 'untrusted';

export type NetworkPolicy = 'allow' | 'deny' | 'allowlist';

export interface ResourceQuota {
  memory_mb: number;
  cpu_quota: number;
  pids_limit: number;
  disk_mb: number;
}

export interface SecurityProfile {
  project_path: string;
  risk_class: RiskClass;
  default_transport: string;
  default_skip_permissions: boolean;
  secrets_scope_id: string | null;
  resource_quota: ResourceQuota;
  network_policy: NetworkPolicy;
  egress_allowlist: string[];
  created_at: string;
  updated_at: string;
}

export type SecurityProfilePayload = Omit<
  SecurityProfile,
  'project_path' | 'created_at' | 'updated_at'
>;

export type SecurityProfilePatch = Partial<SecurityProfilePayload>;

export interface SecurityProfileDeleteResponse {
  project_path: string;
  deleted: boolean;
  recreated_default: SecurityProfile;
}
