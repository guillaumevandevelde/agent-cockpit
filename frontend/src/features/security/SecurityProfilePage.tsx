// Security profile editor for the active project.
//
// Per docs/cockpit/veilig-bouwen-en-uitleveren.md §4.3, every product project
// carries a `ProjectSecurityProfile` that captures risk_class, transport, network
// policy, resource quota and secrets scope. Today the UI is read-mostly with a
// single PATCH form — risk_class changes still log an audit line server-side,
// so the operator sees a toast confirming the save but the UI doesn't need its
// own audit feed (follow-up #10 swaps the log for a queryable table).

import { useCallback, useEffect, useState } from 'react'
import { Shield, ShieldAlert, Save, RotateCcw, Trash2 } from 'lucide-react'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { useProjectContext } from '@/contexts/ProjectContext'
import {
  deleteSecurityProfile,
  getSecurityProfile,
  patchSecurityProfile,
  putSecurityProfile,
} from './api'
import type {
  NetworkPolicy,
  RiskClass,
  SecurityProfile,
} from './types'
import { toast } from 'sonner'

const RISK_CLASS_OPTIONS: { value: RiskClass; label: string }[] = [
  { value: 'meta', label: 'meta — claude-cockpit itself' },
  { value: 'product-staging', label: 'product-staging — safe default' },
  { value: 'product-prod', label: 'product-prod — released / customer-facing' },
  { value: 'untrusted', label: 'untrusted — sandboxed / hostile input' },
]

const NETWORK_POLICY_OPTIONS: { value: NetworkPolicy; label: string }[] = [
  { value: 'allowlist', label: 'allowlist — only listed egress hosts' },
  { value: 'allow', label: 'allow — full egress (least safe)' },
  { value: 'deny', label: 'deny — no egress at all' },
]

const RISK_VARIANT: Record<RiskClass, 'default' | 'secondary' | 'destructive' | 'outline'> = {
  meta: 'secondary',
  'product-staging': 'default',
  'product-prod': 'destructive',
  untrusted: 'destructive',
}

function egressToText(list: string[]): string {
  return list.join('\n')
}

function textToEgress(text: string): string[] {
  return text
    .split(/\r?\n/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

export function SecurityProfilePage() {
  const { activeProject } = useProjectContext()
  const [profile, setProfile] = useState<SecurityProfile | null>(null)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editing, setEditing] = useState(false)

  // Editable form fields — kept separately from `profile` so the displayed
  // version doesn't flicker while typing. `dirty` tracks whether the form
  // diverges from the server so the Save button knows to enable itself.
  const [riskClass, setRiskClass] = useState<RiskClass>('product-staging')
  const [defaultTransport, setDefaultTransport] = useState('sandcastle')
  const [defaultSkipPermissions, setDefaultSkipPermissions] = useState(false)
  const [secretsScopeId, setSecretsScopeId] = useState('')
  const [networkPolicy, setNetworkPolicy] = useState<NetworkPolicy>('allowlist')
  const [egressText, setEgressText] = useState('')
  const [memoryMb, setMemoryMb] = useState(1024)
  const [cpuQuota, setCpuQuota] = useState(1)
  const [pidsLimit, setPidsLimit] = useState(128)
  const [diskMb, setDiskMb] = useState(2048)

  const load = useCallback(
    async (path: string) => {
      setLoading(true)
      try {
        const p = await getSecurityProfile(path)
        setProfile(p)
        setRiskClass(p.risk_class)
        setDefaultTransport(p.default_transport)
        setDefaultSkipPermissions(p.default_skip_permissions)
        setSecretsScopeId(p.secrets_scope_id ?? '')
        setNetworkPolicy(p.network_policy)
        setEgressText(egressToText(p.egress_allowlist))
        setMemoryMb(p.resource_quota.memory_mb)
        setCpuQuota(p.resource_quota.cpu_quota)
        setPidsLimit(p.resource_quota.pids_limit)
        setDiskMb(p.resource_quota.disk_mb)
        setEditing(false)
      } catch (err) {
        setProfile(null)
        toast.error(
          err instanceof Error ? err.message : 'Failed to load security profile'
        )
      } finally {
        setLoading(false)
      }
    },
    []
  )

  useEffect(() => {
    if (activeProject?.path) {
      void load(activeProject.path)
    } else {
      setProfile(null)
    }
  }, [activeProject?.path, load])

  function buildPayload(): Omit<SecurityProfile, 'project_path' | 'created_at' | 'updated_at'> {
    return {
      risk_class: riskClass,
      default_transport: defaultTransport,
      default_skip_permissions: defaultSkipPermissions,
      secrets_scope_id: secretsScopeId || null,
      resource_quota: {
        memory_mb: memoryMb,
        cpu_quota: cpuQuota,
        pids_limit: pidsLimit,
        disk_mb: diskMb,
      },
      network_policy: networkPolicy,
      egress_allowlist: textToEgress(egressText),
    }
  }

  async function handleSave(): Promise<void> {
    if (!activeProject?.path) return
    setSaving(true)
    try {
      const updated = await putSecurityProfile(activeProject.path, buildPayload())
      setProfile(updated)
      setEditing(false)
      toast.success('Security profile saved (PUT replaces all fields).')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save profile')
    } finally {
      setSaving(false)
    }
  }

  async function handlePatch(): Promise<void> {
    if (!activeProject?.path) return
    setSaving(true)
    try {
      // Always include the full payload for PATCH too — PATCH semantics
      // mean only-set-fields are applied, so the server-side validator
      // gets the same shape. Easier mental model than partial diffs.
      const updated = await patchSecurityProfile(activeProject.path, buildPayload())
      setProfile(updated)
      setEditing(false)
      toast.success('Security profile updated.')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update profile')
    } finally {
      setSaving(false)
    }
  }

  function handleReset(): void {
    if (!profile) return
    setRiskClass(profile.risk_class)
    setDefaultTransport(profile.default_transport)
    setDefaultSkipPermissions(profile.default_skip_permissions)
    setSecretsScopeId(profile.secrets_scope_id ?? '')
    setNetworkPolicy(profile.network_policy)
    setEgressText(egressToText(profile.egress_allowlist))
    setMemoryMb(profile.resource_quota.memory_mb)
    setCpuQuota(profile.resource_quota.cpu_quota)
    setPidsLimit(profile.resource_quota.pids_limit)
    setDiskMb(profile.resource_quota.disk_mb)
    setEditing(false)
  }

  async function handleDelete(): Promise<void> {
    if (!activeProject?.path) return
    if (!window.confirm(`Drop the security profile for ${activeProject.path}?`)) {
      return
    }
    setSaving(true)
    try {
      const res = await deleteSecurityProfile(activeProject.path)
      setProfile(res.recreated_default)
      handleReset()
      toast.success(
        res.deleted
          ? 'Security profile deleted — defaults restored on next read.'
          : 'No profile to delete — defaults restored.'
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete profile')
    } finally {
      setSaving(false)
    }
  }

  if (!activeProject) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            Security Profile
          </CardTitle>
          <CardDescription>
            Select an active project to view its security policy.
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Shield className="h-7 w-7" />
            Security Profile
          </h1>
          <p className="text-muted-foreground mt-1">
            Risk class, transport, network policy and resource quota for{' '}
            <span className="font-mono">{activeProject.path}</span>.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <RefreshButton
            onClick={() => void load(activeProject.path)}
            loading={loading}
          />
          <Button
            variant="outline"
            size="sm"
            onClick={handleReset}
            disabled={!editing || saving}
          >
            <RotateCcw className="h-4 w-4 mr-1" />
            Reset
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={() => void handleDelete()}
            disabled={saving}
          >
            <Trash2 className="h-4 w-4 mr-1" />
            Reset to defaults
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Identity</CardTitle>
            <CardDescription>
              Drives dispatch + Sandcastle config decisions.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>risk_class</Label>
              <Select
                value={riskClass}
                onValueChange={(v) => {
                  setRiskClass(v as RiskClass)
                  setEditing(true)
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {RISK_CLASS_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="flex items-center gap-2 pt-1">
                <Badge variant={RISK_VARIANT[riskClass]}>{riskClass}</Badge>
                {riskClass !== profile?.risk_class && (
                  <span className="text-xs text-muted-foreground">
                    (changed from <code>{profile?.risk_class}</code> — audited on save)
                  </span>
                )}
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="sp-default-transport">default_transport</Label>
              <Input
                id="sp-default-transport"
                value={defaultTransport}
                onChange={(e) => {
                  setDefaultTransport(e.target.value)
                  setEditing(true)
                }}
              />
            </div>

            <div className="flex items-center justify-between rounded-md border p-3">
              <div className="space-y-0.5">
                <Label>default_skip_permissions</Label>
                <p className="text-xs text-muted-foreground">
                  When false, the CLI's permission prompts remain on.
                </p>
              </div>
              <Switch
                checked={defaultSkipPermissions}
                onCheckedChange={(v) => {
                  setDefaultSkipPermissions(v)
                  setEditing(true)
                }}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="sp-secrets-scope">secrets_scope_id</Label>
              <Input
                id="sp-secrets-scope"
                placeholder="(empty = no scoped secrets)"
                value={secretsScopeId}
                onChange={(e) => {
                  setSecretsScopeId(e.target.value)
                  setEditing(true)
                }}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Network</CardTitle>
            <CardDescription>
              Egress policy. <code>allowlist</code> is the safe default.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>network_policy</Label>
              <Select
                value={networkPolicy}
                onValueChange={(v) => {
                  setNetworkPolicy(v as NetworkPolicy)
                  setEditing(true)
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {NETWORK_POLICY_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="sp-egress">egress_allowlist</Label>
              <textarea
                id="sp-egress"
                className="flex min-h-[120px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                placeholder={'pypi.org\ngithub.com'}
                value={egressText}
                onChange={(e) => {
                  setEgressText(e.target.value)
                  setEditing(true)
                }}
              />
              <p className="text-xs text-muted-foreground">
                One host per line. Only used when <code>network_policy</code> ={' '}
                <code>allowlist</code>.
              </p>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5" />
            Resource quota
          </CardTitle>
          <CardDescription>
            Caps applied when the session runs in Sandcastle.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-4">
          <div className="space-y-2">
            <Label htmlFor="sp-quota-memory">memory_mb</Label>
            <Input
              id="sp-quota-memory"
              type="number"
              min={1}
              value={memoryMb}
              onChange={(e) => {
                setMemoryMb(Number(e.target.value))
                setEditing(true)
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sp-quota-cpu">cpu_quota</Label>
            <Input
              id="sp-quota-cpu"
              type="number"
              min={1}
              value={cpuQuota}
              onChange={(e) => {
                setCpuQuota(Number(e.target.value))
                setEditing(true)
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sp-quota-pids">pids_limit</Label>
            <Input
              id="sp-quota-pids"
              type="number"
              min={1}
              value={pidsLimit}
              onChange={(e) => {
                setPidsLimit(Number(e.target.value))
                setEditing(true)
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="sp-quota-disk">disk_mb</Label>
            <Input
              id="sp-quota-disk"
              type="number"
              min={1}
              value={diskMb}
              onChange={(e) => {
                setDiskMb(Number(e.target.value))
                setEditing(true)
              }}
            />
          </div>
        </CardContent>
      </Card>

      <div className="flex items-center gap-2 sticky bottom-4 z-10">
        <Button
          onClick={() => void handlePatch()}
          disabled={!editing || saving || loading}
        >
          <Save className="h-4 w-4 mr-2" />
          Save changes (PATCH)
        </Button>
        <Button
          variant="secondary"
          onClick={() => void handleSave()}
          disabled={!editing || saving || loading}
        >
          Replace all (PUT)
        </Button>
        <p className="text-xs text-muted-foreground">
          PATCH keeps untouched server-side fields; PUT resets them to whatever
          is in this form. A <code>risk_class</code> change logs an audit line
          server-side.
        </p>
      </div>
    </div>
  )
}
