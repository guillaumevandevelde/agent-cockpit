import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MessageSquare } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { RefreshButton } from '@/components/shared/RefreshButton'
import { useSessionsApi } from '@/hooks/useSessionsApi'
import { useFetchData } from '@/hooks/useFetchData'
import { useProjectContext } from '@/contexts/ProjectContext'
import { SessionList } from './SessionList'

export function SessionsPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { activeProject } = useProjectContext()
  const { listProjects } = useSessionsApi()

  const [selectedProject, setSelectedProject] = useState<string | null>(
    activeProject?.path || searchParams.get('project') || null
  )

  const { data, loading, refresh: loadProjects } = useFetchData(
    () => listProjects(),
    [listProjects],
    (message) => console.error('Failed to load projects:', message)
  )
  const projects = data?.projects ?? []

  const handleProjectChange = (value: string) => {
    const folder = value === 'all' ? null : value
    setSelectedProject(folder)
    setSearchParams(folder ? { project: folder } : {})
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <MessageSquare className="h-8 w-8" />
            Session Transcripts
          </h1>
          <p className="text-muted-foreground">
            View your Claude Code conversation history
          </p>
        </div>
        <RefreshButton onClick={loadProjects} loading={loading} />
      </div>

      {/* Project Filter */}
      <Card>
        <CardHeader>
          <CardTitle>Filter by Project</CardTitle>
          <CardDescription>
            Select a project to view its sessions, or view all
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Select value={selectedProject || 'all'} onValueChange={handleProjectChange}>
            <SelectTrigger className="w-[300px]">
              <SelectValue placeholder="All projects" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Projects</SelectItem>
              {projects.map(p => (
                <SelectItem key={p.folder} value={p.folder}>
                  {p.name} ({p.session_count})
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {/* Session List */}
      <SessionList projectFolder={selectedProject} />
    </div>
  )
}
