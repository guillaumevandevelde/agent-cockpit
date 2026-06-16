import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Folder, FolderOpen, ChevronRight, ArrowLeft } from 'lucide-react';
import { apiClient } from '@/lib/api';
import { MODAL_SIZES } from '@/lib/constants';

interface BrowseResult {
  path: string;
  parent: string | null;
  directories: string[];
}

interface DirectoryBrowserDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Path to open the browser at (defaults to "~"). */
  initialPath?: string;
  /** Called with the chosen absolute path when the user confirms. */
  onSelect: (path: string) => void;
}

export function DirectoryBrowserDialog({
  open,
  onOpenChange,
  initialPath,
  onSelect,
}: DirectoryBrowserDialogProps) {
  const [result, setResult] = useState<BrowseResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const browseTo = async (path: string) => {
    setLoading(true);
    setError(null);
    try {
      const r = await apiClient<BrowseResult>(
        `projects/browse?path=${encodeURIComponent(path)}`
      );
      setResult(r);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to read directory');
    } finally {
      setLoading(false);
    }
  };

  // Browse to the starting path each time the dialog is opened.
  useEffect(() => {
    if (open) {
      browseTo(initialPath?.trim() || '~');
    }
  }, [open, initialPath]);

  const handleSelect = () => {
    if (result) {
      onSelect(result.path);
    }
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={MODAL_SIZES.SM}>
        <DialogHeader>
          <DialogTitle>Browse Directories</DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          {/* Current path breadcrumb */}
          {result && (
            <div className="flex items-center gap-1 text-sm text-muted-foreground bg-muted px-3 py-2 rounded-md min-w-0">
              <FolderOpen className="h-4 w-4 shrink-0" />
              <span className="truncate">{result.path}</span>
            </div>
          )}

          {/* Up / parent button */}
          {result?.parent && (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start gap-2 text-muted-foreground"
              onClick={() => browseTo(result.parent!)}
            >
              <ArrowLeft className="h-4 w-4" />
              <span className="truncate">.. (up to {result.parent})</span>
            </Button>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}

          {loading && (
            <p className="text-sm text-muted-foreground text-center py-4">Loading…</p>
          )}

          {/* Directory list */}
          {!loading && result && (
            result.directories.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No subdirectories
              </p>
            ) : (
              <ScrollArea className="h-64 rounded-md border">
                <div className="p-1">
                  {result.directories.map((dir) => (
                    <button
                      key={dir}
                      type="button"
                      className="w-full flex items-center gap-2 px-3 py-2 rounded text-sm hover:bg-accent hover:text-accent-foreground transition-colors text-left"
                      onClick={() => browseTo(`${result.path}/${dir}`)}
                    >
                      <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">{dir}</span>
                      <ChevronRight className="h-4 w-4 shrink-0 ml-auto text-muted-foreground" />
                    </button>
                  ))}
                </div>
              </ScrollArea>
            )
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSelect} disabled={!result}>
            Use this directory
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
