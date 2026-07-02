export interface Host {
  id: number
  alias: string
  hostname: string
  port: number
  username: string
  ssh_key_path: string | null
  status: 'unknown' | 'online' | 'offline'
  created_at: string | null
  updated_at: string | null
}

export interface HostCreateRequest {
  alias: string
  hostname: string
  port?: number
  username: string
  ssh_key_path?: string | null
}

export interface HostUpdateRequest {
  alias?: string
  hostname?: string
  port?: number
  username?: string
  ssh_key_path?: string | null
}

export interface HostTestResponse {
  reachable: boolean
  alias: string
  hostname: string
}

export interface HostListResponse {
  hosts: Host[]
}
