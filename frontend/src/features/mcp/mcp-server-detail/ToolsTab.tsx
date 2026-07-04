import { useState } from "react";
import { Wrench, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ToolDetailView } from "./ToolDetailView";
import type { MCPServer, MCPTestConnectionResponse } from "@/types/mcp";

// --- Tools Tab ---
export function ToolsTab({
  server,
  testResult,
}: {
  server: MCPServer;
  testResult: MCPTestConnectionResponse | null;
}) {
  const [filter, setFilter] = useState("");
  const [expandedTool, setExpandedTool] = useState<string | null>(null);

  const tools = testResult?.tools || server.tools || [];
  const toolCount =
    testResult?.tools?.length ??
    server.tool_count ??
    tools.length;
  const totalCount = toolCount;
  const isTruncated = totalCount > tools.length;

  const filteredTools = tools.filter(
    (tool) =>
      tool.name.toLowerCase().includes(filter.toLowerCase()) ||
      tool.description?.toLowerCase().includes(filter.toLowerCase())
  );

  if (tools.length === 0) {
    return (
      <div className="h-[60vh] flex flex-col items-center justify-center text-center">
        <Wrench className="h-8 w-8 text-muted-foreground mb-2" />
        <p className="text-muted-foreground">
          {server.last_tested_at
            ? "No tools discovered."
            : "Click Test in the Overview tab to discover tools."}
        </p>
      </div>
    );
  }

  return (
    <div className="h-[60vh] flex flex-col gap-3">
      {tools.length > 5 && (
        <div className="relative shrink-0">
          <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Filter tools..."
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="pl-8 h-9"
          />
        </div>
      )}
      {isTruncated && (
        <p className="text-xs text-muted-foreground shrink-0">
          Showing {tools.length} of {totalCount} tools
        </p>
      )}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-2 pr-4">
          {filteredTools.map((tool) => (
            <div key={tool.name}>
              <button
                className="w-full text-left bg-background rounded p-2.5 text-sm hover:bg-accent transition-colors cursor-pointer border"
                onClick={() =>
                  setExpandedTool(
                    expandedTool === tool.name ? null : tool.name
                  )
                }
              >
                <div className="font-medium font-mono text-xs">
                  {tool.name}
                </div>
                {tool.description && (
                  <div className="text-xs text-muted-foreground mt-1 line-clamp-2">
                    {tool.description}
                  </div>
                )}
              </button>
              {expandedTool === tool.name && (
                <div className="ml-2 mt-1">
                  <ToolDetailView tool={tool} />
                </div>
              )}
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
