/**
 * Projects management page
 */
import { useState } from 'react';
import { FolderOpen, FolderPlus, Sparkles, ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useProjectContext } from '@/contexts/ProjectContext';
import { ProjectList } from './ProjectList';
import { ProjectDiscovery } from './ProjectDiscovery';
import { AddProjectDialog } from './AddProjectDialog';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button, buttonVariants } from '@/components/ui/button';
import { RefreshButton } from '@/components/shared/RefreshButton';

// Link to the canonical doc that explains the spec-driven new-project flow.
// The flow lives in `docs/cockpit/new-project-startup-flow.md` and is
// surfaced here so a user landing on this page knows there are TWO ways to
// get a project into the list: "birth" (spec-driven intake → Promote) versus
// "track" (Add Folder for an existing directory).
const SPEC_DRIVEN_FLOW_DOC_URL =
  'https://github.com/guillaumevandevelde/claude-cockpit/blob/master/docs/cockpit/new-project-startup-flow.md';

export function ProjectsPage() {
  const { projects, loading, error, fetchProjects } = useProjectContext();
  const [showDiscovery, setShowDiscovery] = useState(false);
  const [showAddFolder, setShowAddFolder] = useState(false);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <FolderOpen className="h-8 w-8" />
            Projects
          </h1>
          <p className="text-muted-foreground mt-2">
            Manage local project directories
          </p>
        </div>
        <div className="flex gap-2">
          <RefreshButton onClick={fetchProjects} loading={loading} />
          <Button variant="outline" onClick={() => setShowAddFolder(true)}>
            <FolderPlus className="h-4 w-4" />
            Add Folder
          </Button>
          <Button onClick={() => setShowDiscovery(!showDiscovery)}>
            {showDiscovery ? 'Hide Discovery' : 'Discover Projects'}
          </Button>
        </div>
      </div>

      {/* Discoverability hint — distinguishes "birth a new spec-driven project"
          (intake → spec → Promote → new repo with seeded .claude/) from
          "track an existing folder" (Add Folder / Discover above). A user who
          came here to start a new app-idea used to have to dig through
          ~8 docs + a skill + a kanban button; the flow is fully built but
          the entry point was hidden. See
          docs/cockpit/new-project-startup-flow.md §4. */}
      <Card className="border-amber-500/40 bg-amber-50/40 dark:bg-amber-950/20">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Sparkles className="h-5 w-5 text-amber-500" />
            Want to build a new app-idea, spec-driven?
          </CardTitle>
          <CardDescription>
            The &ldquo;Add Folder&rdquo; button above is for{' '}
            <em>tracking an existing folder</em>. To <em>birth a new
            spec-driven project</em> (intake-kaart → design-doc →
            plan → Promote into a fresh git-repo with seeded{' '}
            <code>.claude/</code>), start on the Kanban board&apos;s{' '}
            <strong>intake</strong> column.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Link
            to="/kanban"
            className={buttonVariants({ variant: 'default' }) + ' gap-2'}
          >
            Go to Kanban &mdash; intake column
            <ArrowRight className="h-4 w-4" />
          </Link>
          <a
            href={SPEC_DRIVEN_FLOW_DOC_URL}
            target="_blank"
            rel="noopener noreferrer"
            className={buttonVariants({ variant: 'outline' }) + ' gap-2'}
          >
            Read the full flow
            <ArrowRight className="h-4 w-4" />
          </a>
        </CardContent>
      </Card>

      {showDiscovery && (
        <ProjectDiscovery onProjectsDiscovered={() => {
          setShowDiscovery(false);
        }} />
      )}

      <AddProjectDialog open={showAddFolder} onOpenChange={setShowAddFolder} />

      {error && (
        <Card className="border-destructive">
          <CardHeader>
            <CardTitle className="text-destructive">Error</CardTitle>
          </CardHeader>
          <CardContent>
            <p>{error}</p>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Tracked Projects</CardTitle>
          <CardDescription>
            {projects.length} project{projects.length !== 1 ? 's' : ''} tracked
          </CardDescription>
        </CardHeader>
        <CardContent>
          <ProjectList projects={projects} loading={loading} />
        </CardContent>
      </Card>
    </div>
  );
}
