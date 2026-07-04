import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Bot, FileCode, Package, Puzzle, Server, Terminal } from "lucide-react";
import { type RestorePlan } from "@/types/backup";

interface ContentsStepProps {
  plan: RestorePlan | null;
  groupedFiles: {
    skills: string[];
    plugins: string[];
    mcp: string[];
    agents: string[];
    commands: string[];
    other: string[];
  };
}

export function ContentsStep({ plan, groupedFiles }: ContentsStepProps) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        This backup contains {plan?.files_to_restore.length || 0} files:
      </p>

      <ScrollArea className="h-[300px] border rounded-lg p-3">
        <div className="space-y-4">
          {groupedFiles.skills.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <Package className="h-4 w-4 text-green-600" />
                Skills ({groupedFiles.skills.length})
              </h4>
              {plan?.skills_to_restore.map((skill) => (
                <div key={skill.name} className="ml-6 py-1 text-sm">
                  <span className="font-mono">{skill.name}</span>
                  {skill.dependencies.length > 0 && (
                    <span className="ml-2 text-muted-foreground">
                      ({skill.dependencies.length} deps)
                    </span>
                  )}
                </div>
              ))}
            </div>
          )}

          {groupedFiles.plugins.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <Puzzle className="h-4 w-4 text-purple-600" />
                Plugins ({plan?.plugins_to_restore.length})
              </h4>
              {plan?.plugins_to_restore.map((plugin) => (
                <div key={plugin.name} className="ml-6 py-1 text-sm">
                  <span className="font-mono">{plugin.name}</span>
                  {plugin.version && (
                    <span className="ml-2 text-muted-foreground">v{plugin.version}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          {groupedFiles.mcp.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <Server className="h-4 w-4 text-blue-600" />
                MCP Servers ({plan?.mcp_servers_to_restore.length})
              </h4>
              {plan?.mcp_servers_to_restore.map((server) => (
                <div key={server.name} className="ml-6 py-1 text-sm">
                  <span className="font-mono">{server.name}</span>
                  <Badge variant="outline" className="ml-2 text-xs">
                    {server.type}
                  </Badge>
                </div>
              ))}
            </div>
          )}

          {groupedFiles.agents.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <Bot className="h-4 w-4 text-orange-600" />
                Agents ({groupedFiles.agents.length})
              </h4>
              {groupedFiles.agents.map((file) => (
                <div key={file} className="ml-6 py-1 text-sm font-mono text-muted-foreground">
                  {file.split("/").pop()}
                </div>
              ))}
            </div>
          )}

          {groupedFiles.commands.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <Terminal className="h-4 w-4 text-cyan-600" />
                Commands ({groupedFiles.commands.length})
              </h4>
              {groupedFiles.commands.map((file) => (
                <div key={file} className="ml-6 py-1 text-sm font-mono text-muted-foreground">
                  {file.split("/").pop()}
                </div>
              ))}
            </div>
          )}

          {groupedFiles.other.length > 0 && (
            <div>
              <h4 className="font-medium flex items-center gap-2 mb-2">
                <FileCode className="h-4 w-4 text-gray-600" />
                Config Files ({groupedFiles.other.length})
              </h4>
              {groupedFiles.other.map((file) => (
                <div key={file} className="ml-6 py-1 text-sm font-mono text-muted-foreground">
                  {file}
                </div>
              ))}
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}
