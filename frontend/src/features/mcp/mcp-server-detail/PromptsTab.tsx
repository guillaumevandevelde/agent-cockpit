import { MessageSquare } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { MCPServer, MCPTestConnectionResponse, MCPPrompt } from "@/types/mcp";

// --- Prompts Tab ---
export function PromptsTab({
  server,
  testResult,
}: {
  server: MCPServer;
  testResult: MCPTestConnectionResponse | null;
}) {
  const prompts: MCPPrompt[] = testResult?.prompts || server.prompts || [];
  const promptCount =
    testResult?.prompt_count ?? server.prompt_count ?? prompts.length;
  const isTruncated = promptCount > prompts.length;
  const capabilities = testResult?.capabilities ?? server.capabilities;
  const hasPromptCapability = !!capabilities?.prompts;

  if (prompts.length === 0) {
    let message: string;
    if (!server.last_tested_at && !testResult) {
      message = "Test connection to discover prompts.";
    } else if (capabilities && !hasPromptCapability) {
      message = "This server does not expose prompts.";
    } else {
      message = "No prompts found.";
    }

    return (
      <div className="h-[60vh] flex flex-col items-center justify-center text-center">
        <MessageSquare className="h-8 w-8 text-muted-foreground mb-2" />
        <p className="text-muted-foreground">{message}</p>
      </div>
    );
  }

  return (
    <div className="h-[60vh] flex flex-col gap-3">
      {isTruncated && (
        <p className="text-xs text-muted-foreground shrink-0">
          Showing {prompts.length} of {promptCount} prompts
        </p>
      )}
      <ScrollArea className="flex-1 min-h-0">
        <div className="space-y-2 pr-4">
          {prompts.map((prompt, i) => (
            <div
              key={i}
              className="bg-background rounded p-2.5 text-sm border"
            >
              <div className="font-medium">{prompt.name}</div>
              {prompt.description && (
                <div className="text-xs text-muted-foreground mt-1">
                  {prompt.description}
                </div>
              )}
              {prompt.arguments && prompt.arguments.length > 0 && (
                <div className="mt-2 space-y-1">
                  <div className="text-xs font-medium text-muted-foreground">
                    Arguments
                  </div>
                  {prompt.arguments.map((arg, j) => (
                    <div key={j} className="flex items-center gap-1.5 text-xs">
                      <span className="font-mono">{arg.name}</span>
                      {arg.required && (
                        <Badge
                          variant="destructive"
                          className="text-[10px] px-1 py-0"
                        >
                          required
                        </Badge>
                      )}
                      {arg.description && (
                        <span className="text-muted-foreground">
                          - {arg.description}
                        </span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}
