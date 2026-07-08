import { useState, useEffect } from 'react';
import { useProjectContext } from '@/contexts/ProjectContext';
import type { ProjectBase } from '@/types/projects';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Folder } from 'lucide-react';
import { apiClient } from '@/lib/api';
import { DirectoryBrowserDialog } from './DirectoryBrowserDialog';

interface BrowseResult {
  path: string;
  parent: string | null;
  directories: string[];
}

interface ProjectDiscoveryProps {
  onProjectsDiscovered: () => void;
}

export function ProjectDiscovery({ onProjectsDiscovered }: ProjectDiscoveryProps) {
  const { discoverProjects, addProject } = useProjectContext();
  const [searchPath, setSearchPath] = useState('');
  const [discoveredProjects, setDiscoveredProjects] = useState<ProjectBase[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendAvailable, setBackendAvailable] = useState(true);
  const [addingProjects, setAddingProjects] = useState<Set<string>>(new Set());

  // Pre-populate with the real home path on mount
  useEffect(() => {
    apiClient<BrowseResult>('projects/browse?path=~')
      .then((r) => {
        setSearchPath(r.path);
        setBackendAvailable(true);
      })
      .catch(() => setBackendAvailable(false));
  }, []);

  // Directory browser state
  const [browserOpen, setBrowserOpen] = useState(false);

  const handleDiscover = async () => {
    if (!searchPath.trim()) {
      setError('Please enter a path to search');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const discovered = await discoverProjects(searchPath);
      setDiscoveredProjects(discovered);
      if (discovered.length === 0) {
        setError('No project folders found in this directory');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to discover projects');
    } finally {
      setLoading(false);
    }
  };

  const handleAddProject = async (project: ProjectBase) => {
    setAddingProjects(prev => new Set(prev).add(project.path));
    try {
      await addProject(project);
      setDiscoveredProjects(prev => prev.filter(p => p.path !== project.path));
      if (discoveredProjects.length === 1) {
        onProjectsDiscovered();
      }
    } catch (err) {
      console.error('Failed to add project:', err);
    } finally {
      setAddingProjects(prev => {
        const next = new Set(prev);
        next.delete(project.path);
        return next;
      });
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Discover Projects</CardTitle>
        <CardDescription>
          Enter a directory path to find project folders
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!backendAvailable && (
          <div className="text-sm text-destructive">
            Backend not available — start the backend server and reload to discover projects.
          </div>
        )}
        <div className="flex gap-2">
          <Input
            type="text"
            value={searchPath}
            onChange={(e) => setSearchPath(e.target.value)}
            placeholder="~/projects"
            onKeyDown={(e) => { if (e.key === 'Enter') handleDiscover(); }}
          />
          <Button variant="outline" onClick={() => setBrowserOpen(true)} title="Browse directories">
            <Folder className="h-4 w-4" />
          </Button>
          <Button onClick={handleDiscover} disabled={loading}>
            {loading ? 'Searching...' : 'Search'}
          </Button>
        </div>

        {error && (
          <div className="text-sm text-destructive">{error}</div>
        )}

        {discoveredProjects.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-sm font-medium">
                Found {discoveredProjects.length} project{discoveredProjects.length !== 1 ? 's' : ''}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  for (const project of discoveredProjects) {
                    await handleAddProject(project);
                  }
                }}
              >
                Add All
              </Button>
            </div>
            <div className="space-y-2">
              {discoveredProjects.map((project) => (
                <Card key={project.path}>
                  <CardContent className="pt-4">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <p className="font-medium">{project.name}</p>
                          {project.source === "configured" ? (
                            <Badge variant="outline" className="border-green-500 text-green-600">Configured</Badge>
                          ) : project.source === "session_history" ? (
                            <Badge variant="outline" className="border-blue-500 text-blue-600">Session History</Badge>
                          ) : project.source === "directory" ? (
                            <Badge variant="outline">Folder</Badge>
                          ) : (
                            <Badge variant="outline">Discovered</Badge>
                          )}
                        </div>
                        <p className="text-sm text-muted-foreground mt-1">{project.path}</p>
                      </div>
                      <Button
                        size="sm"
                        onClick={() => handleAddProject(project)}
                        disabled={addingProjects.has(project.path)}
                      >
                        {addingProjects.has(project.path) ? 'Adding...' : 'Add'}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          </div>
        )}
      </CardContent>

      {/* Directory browser dialog */}
      <DirectoryBrowserDialog
        open={browserOpen}
        onOpenChange={setBrowserOpen}
        initialPath={searchPath}
        onSelect={setSearchPath}
      />
    </Card>
  );
}
