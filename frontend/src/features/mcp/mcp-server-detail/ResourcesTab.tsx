import { FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { MCPServer, MCPTestConnectionResponse, MCPResource } from "@/types/mcp";

// --- Resources Tab ---
export function ResourcesTab({
  server,
  testResult,
}: {
  server: MCPServer;
  testResult: MCPTestConnectionResponse | null;
}) {
  const resources: MCPResource[] = testResult?.resources || server.resources || [];
  const resourceCount =
    testResult?.resource_count ?? server.resource_count ?? resources.length;
  const isTruncated = resourceCount > resources.length;
  const capabilities = testResult?.capabilities ?? server.capabilities;
  const hasResourceCapability = !!capabilities?.resources;

  if (resources.length === 0) {
    let message: string;
    if (!server.last_tested_at && !testResult) {
      message = "Test connection to discover resources.";
    } else if (capabilities && !hasResourceCapability) {
      message = "This server does not expose resources.";
    } else {
      message = "No resources found.";
    }

    return (
      <div className="h-[60vh] flex flex-col items-center justify-center text-center">
        <FileText className="h-8 w-8 text-muted-foreground mb-2" />
        <p className="text-muted-foreground">{message}</p>
      </div>
    );
  }

  return (
    <div className="h-[60vh] flex flex-col gap-3">
      {isTruncated && (
        <p className="text-xs text-muted-foreground shrink-0">
          Showing {resources.length} of {resourceCount} resources
        </p>
      )}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-2 pr-4">
          {resources.map((resource, i) => (
            <div
              key={i}
              className="bg-background rounded p-2.5 text-sm border"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{resource.name}</div>
                  <div className="font-mono text-xs text-muted-foreground truncate">
                    {resource.uri}
                  </div>
                  {resource.description && (
                    <div className="text-xs text-muted-foreground mt-1">
                      {resource.description}
                    </div>
                  )}
                </div>
                {resource.mimeType && (
                  <Badge variant="outline" className="text-xs shrink-0">
                    {resource.mimeType}
                  </Badge>
                )}
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
