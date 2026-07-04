import { Check, Info } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { HOOK_EVENTS, type HookEvent, type HookType } from "@/types/hooks";
import { getTypeIcon } from "./getTypeIcon";

interface AdvancedStepProps {
  scope: "user" | "project";
  onScopeChange: (scope: "user" | "project") => void;
  asyncRun: boolean;
  onAsyncRunChange: (value: boolean) => void;
  once: boolean;
  onOnceChange: (value: boolean) => void;
  statusMessage: string;
  onStatusMessageChange: (value: string) => void;
  timeout: number | undefined;
  onTimeoutChange: (value: number | undefined) => void;
  type: HookType;
  event: HookEvent;
  matcher: string;
  model: string;
}

export function AdvancedStep({
  scope,
  onScopeChange,
  asyncRun,
  onAsyncRunChange,
  once,
  onOnceChange,
  statusMessage,
  onStatusMessageChange,
  timeout,
  onTimeoutChange,
  type,
  event,
  matcher,
  model,
}: AdvancedStepProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium mb-2">
          Step 4: Scope and Advanced Options
        </h3>
        <p className="text-sm text-muted-foreground">
          Configure where the hook is stored and additional settings
        </p>
      </div>

      {/* Scope Selection */}
      <div className="space-y-2">
        <Label>Scope</Label>
        <div className="grid grid-cols-2 gap-3">
          <button
            onClick={() => onScopeChange("user")}
            className={`p-4 border-2 rounded-lg text-left transition-all ${
              scope === "user"
                ? "border-primary bg-primary/5"
                : "border-muted hover:border-primary/50"
            }`}
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium mb-1">User</div>
                <p className="text-sm text-muted-foreground">
                  ~/.claude/settings.json
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Available in all projects
                </p>
              </div>
              {scope === "user" && (
                <Check className="h-5 w-5 text-primary flex-shrink-0 ml-2" />
              )}
            </div>
          </button>

          <button
            onClick={() => onScopeChange("project")}
            className={`p-4 border-2 rounded-lg text-left transition-all ${
              scope === "project"
                ? "border-primary bg-primary/5"
                : "border-muted hover:border-primary/50"
            }`}
          >
            <div className="flex items-start justify-between">
              <div>
                <div className="font-medium mb-1">Project</div>
                <p className="text-sm text-muted-foreground">
                  .claude/settings.json
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Only in this project
                </p>
              </div>
              {scope === "project" && (
                <Check className="h-5 w-5 text-primary flex-shrink-0 ml-2" />
              )}
            </div>
          </button>
        </div>
      </div>

      {/* Advanced Options */}
      <div className="space-y-4 border rounded-lg p-4">
        <h4 className="font-medium">Advanced Options</h4>

        {/* Async toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="async-wizard">Run Async</Label>
            <p className="text-sm text-muted-foreground">
              Run the hook in the background without blocking
            </p>
          </div>
          <Switch
            id="async-wizard"
            checked={asyncRun}
            onCheckedChange={onAsyncRunChange}
          />
        </div>

        {/* Once toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="once-wizard">Run Once</Label>
            <p className="text-sm text-muted-foreground">
              Only run this hook once per session
            </p>
          </div>
          <Switch
            id="once-wizard"
            checked={once}
            onCheckedChange={onOnceChange}
          />
        </div>

        {/* Status Message */}
        <div className="space-y-2">
          <Label htmlFor="status-message-wizard">
            Status Message (optional)
          </Label>
          <Input
            id="status-message-wizard"
            value={statusMessage}
            onChange={(e) => onStatusMessageChange(e.target.value)}
            placeholder="Custom spinner message..."
          />
          <p className="text-sm text-muted-foreground">
            Custom message to show while the hook is running.
          </p>
        </div>

        {/* Timeout (only for command type) */}
        {type === "command" && (
          <div className="space-y-2">
            <Label htmlFor="timeout-wizard">
              Timeout (seconds, optional)
            </Label>
            <Input
              id="timeout-wizard"
              type="number"
              min="1"
              max="300"
              value={timeout || ""}
              onChange={(e) =>
                onTimeoutChange(
                  e.target.value ? parseInt(e.target.value) : undefined
                )
              }
              placeholder="30"
            />
            <p className="text-sm text-muted-foreground">
              Command will be killed if it runs longer than this timeout.
            </p>
          </div>
        )}
      </div>

      {/* Review Summary */}
      <div className="bg-muted p-4 rounded-lg space-y-2">
        <h4 className="font-medium flex items-center gap-2">
          <Info className="h-4 w-4" />
          Review Your Hook
        </h4>
        <div className="space-y-1 text-sm">
          <div>
            <span className="text-muted-foreground">Event:</span>{" "}
            <Badge variant="secondary">
              {HOOK_EVENTS.find((e) => e.name === event)?.label}
            </Badge>
          </div>
          {matcher && (
            <div>
              <span className="text-muted-foreground">Matcher:</span>{" "}
              <code className="bg-background px-2 py-1 rounded">
                {matcher}
              </code>
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Type:</span>{" "}
            <Badge variant="outline" className="flex items-center gap-1">
              {getTypeIcon(type)}
              {type}
            </Badge>
          </div>
          {type === "agent" && (
            <div>
              <span className="text-muted-foreground">Model:</span>{" "}
              <Badge variant="outline">{model}</Badge>
            </div>
          )}
          <div>
            <span className="text-muted-foreground">Scope:</span>{" "}
            <Badge>{scope}</Badge>
          </div>
          {asyncRun && (
            <div>
              <Badge variant="outline">Async</Badge>
            </div>
          )}
          {once && (
            <div>
              <Badge variant="outline">Once per session</Badge>
            </div>
          )}
          {statusMessage && (
            <div>
              <span className="text-muted-foreground">Status:</span>{" "}
              "{statusMessage}"
            </div>
          )}
          {timeout && (
            <div>
              <span className="text-muted-foreground">Timeout:</span>{" "}
              {timeout}s
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
