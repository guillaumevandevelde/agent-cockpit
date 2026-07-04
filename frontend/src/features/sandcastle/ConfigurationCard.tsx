import { AlertTriangle } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { shortPath } from './shortPath'
import type { SandcastleConfig } from './types'

interface ConfigurationCardProps {
  config: SandcastleConfig
  onToggle: () => void
  onUpdateConfig: (updates: Partial<SandcastleConfig>) => void
}

export function ConfigurationCard({ config, onToggle, onUpdateConfig }: ConfigurationCardProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>{shortPath(config.project_path)}</CardDescription>
          </div>
          <Switch
            checked={config.enabled}
            onCheckedChange={onToggle}
          />
        </div>
      </CardHeader>
      {config.enabled && (
        <CardContent className="space-y-4">
          {config.sandbox_provider === 'no-sandbox' && (
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                <strong>No container isolation.</strong> Agents run directly on the host with full filesystem access. Switch to Docker or Podman for true sandbox isolation.
              </AlertDescription>
            </Alert>
          )}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Sandbox Provider</Label>
              <Select
                value={config.sandbox_provider}
                onValueChange={(v) => onUpdateConfig({ sandbox_provider: v as SandcastleConfig['sandbox_provider'] })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="no-sandbox">No Sandbox</SelectItem>
                  <SelectItem value="docker">Docker</SelectItem>
                  <SelectItem value="podman">Podman</SelectItem>
                  <SelectItem value="vercel">Vercel</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Agent Provider</Label>
              <Select
                value={config.agent_provider}
                onValueChange={(v) => onUpdateConfig({ agent_provider: v as SandcastleConfig['agent_provider'] })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="claude-code">Claude Code</SelectItem>
                  <SelectItem value="codex-cli">Codex CLI</SelectItem>
                  <SelectItem value="open-code">Open Code</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Branch Strategy</Label>
              <Select
                value={config.branch_strategy}
                onValueChange={(v) => onUpdateConfig({ branch_strategy: v as SandcastleConfig['branch_strategy'] })}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="head">Head (direct write)</SelectItem>
                  <SelectItem value="merge-to-head">Merge to Head</SelectItem>
                  <SelectItem value="branch">Named Branch</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Docker Image (optional)</Label>
              <input
                type="text"
                value={config.docker_image || ''}
                onChange={(e) => onUpdateConfig({ docker_image: e.target.value || null })}
                placeholder="sandcastle:local"
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              />
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  )
}
