import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { SandcastleHealth } from './types'

interface HealthStatusCardProps {
  health: SandcastleHealth
  onBuildImage: () => void
}

export function HealthStatusCard({ health, onBuildImage }: HealthStatusCardProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">System Health</CardTitle>
          {health.docker_available && !health.docker_image_exists && (
            <Button variant="outline" size="sm" onClick={onBuildImage}>
              Build Docker Image
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="flex flex-wrap gap-4">
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${health.node_available ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-sm">Node.js {health.node_version || '(not found)'}</span>
          </div>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${health.docker_available ? 'bg-green-500' : 'bg-yellow-500'}`} />
            <span className="text-sm">Docker {health.docker_version || '(not found)'}</span>
          </div>
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${health.podman_available ? 'bg-green-500' : 'bg-yellow-500'}`} />
            <span className="text-sm">Podman {health.podman_version || '(not found)'}</span>
          </div>
          {health.docker_available && (
            <div className="flex items-center gap-2">
              <div className={`h-2 w-2 rounded-full ${health.docker_image_exists ? 'bg-green-500' : 'bg-yellow-500'}`} />
              <span className="text-sm">Docker Image {health.docker_image_exists ? '(built)' : '(not built)'}</span>
            </div>
          )}
          <div className="flex items-center gap-2">
            <div className={`h-2 w-2 rounded-full ${health.npm_dependencies_installed ? 'bg-green-500' : 'bg-red-500'}`} />
            <span className="text-sm">npm deps {health.npm_dependencies_installed ? '(installed)' : '(missing)'}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
