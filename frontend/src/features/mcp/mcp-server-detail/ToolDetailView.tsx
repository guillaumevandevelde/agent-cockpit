import { useState } from "react";
import type { MCPTool } from "@/types/mcp";

// --- Tool Detail Section ---
export function ToolDetailView({ tool }: { tool: MCPTool }) {
  const [showRaw, setShowRaw] = useState(false);
  return (
    <div className="border rounded-md p-3 space-y-2 bg-muted/30">
      <div className="font-mono text-sm font-medium">{tool.name}</div>
      {tool.description && (
        <p className="text-sm text-muted-foreground">{tool.description}</p>
      )}
      {tool.inputSchema?.properties && (
        <div>
          <div className="text-xs font-medium text-muted-foreground mb-1">
            Parameters
          </div>
          {Object.entries(tool.inputSchema.properties).map(([key, prop]) => (
            <div key={key} className="text-sm mb-1">
              <span className="font-mono font-medium">{key}</span>
              {tool.inputSchema?.required?.includes(key) && (
                <span className="text-red-500 ml-1">*</span>
              )}
              <span className="text-muted-foreground ml-2">({prop.type})</span>
              {prop.description && (
                <div className="text-xs text-muted-foreground ml-4 mt-0.5">
                  {prop.description}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      {tool.inputSchema && (
        <div>
          <button
            className="text-xs text-muted-foreground hover:text-foreground cursor-pointer"
            onClick={() => setShowRaw(!showRaw)}
          >
            {showRaw ? "Hide" : "View"} raw schema
          </button>
          {showRaw && (
            <pre className="mt-1 bg-background rounded p-2 overflow-x-auto text-xs">
              {JSON.stringify(tool.inputSchema, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
