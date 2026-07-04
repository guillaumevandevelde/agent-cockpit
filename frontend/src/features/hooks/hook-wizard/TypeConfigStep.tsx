import { ChevronDown, ChevronRight, Info, Terminal, MessageSquare, Bot } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  HOOK_TEMPLATES,
  HOOK_ENV_VARS,
  AGENT_MODELS,
  type HookType,
  type HookTemplate,
} from "@/types/hooks";
import { getTypeIcon } from "./getTypeIcon";

interface TypeConfigStepProps {
  type: HookType;
  onTypeChange: (type: HookType) => void;
  command: string;
  onCommandChange: (value: string) => void;
  prompt: string;
  onPromptChange: (value: string) => void;
  model: string;
  onModelChange: (value: string) => void;
  showEnvHelp: boolean;
  onToggleEnvHelp: () => void;
  onApplyTemplate: (template: HookTemplate) => void;
}

export function TypeConfigStep({
  type,
  onTypeChange,
  command,
  onCommandChange,
  prompt,
  onPromptChange,
  model,
  onModelChange,
  showEnvHelp,
  onToggleEnvHelp,
  onApplyTemplate,
}: TypeConfigStepProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium mb-2">
          Step 3: Choose Type and Configure
        </h3>
        <p className="text-sm text-muted-foreground">
          Select the hook type and configure its behavior
        </p>
      </div>

      {/* Type Toggle */}
      <div className="flex gap-2">
        <Button
          type="button"
          variant={type === "command" ? "default" : "outline"}
          className="flex-1"
          onClick={() => onTypeChange("command")}
        >
          <Terminal className="h-4 w-4 mr-2" />
          Command
        </Button>
        <Button
          type="button"
          variant={type === "prompt" ? "default" : "outline"}
          className="flex-1"
          onClick={() => onTypeChange("prompt")}
        >
          <MessageSquare className="h-4 w-4 mr-2" />
          Prompt
        </Button>
        <Button
          type="button"
          variant={type === "agent" ? "default" : "outline"}
          className="flex-1"
          onClick={() => onTypeChange("agent")}
        >
          <Bot className="h-4 w-4 mr-2" />
          Agent
        </Button>
      </div>

      {/* Type description */}
      <p className="text-sm text-muted-foreground">
        {type === "command" && "Execute a shell command when the hook triggers."}
        {type === "prompt" && "Append a prompt to Claude's context when the hook triggers."}
        {type === "agent" && "Spawn a subagent to process the hook with a specific model."}
      </p>

      {/* Templates */}
      <div className="space-y-2">
        <Label>Quick Start Templates</Label>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          {HOOK_TEMPLATES.filter(
            (t) => t.type === type || t.name === "Blank Hook"
          ).map((template) => (
            <button
              key={template.name}
              onClick={() => onApplyTemplate(template)}
              className="p-3 border rounded-lg text-left hover:bg-muted transition-colors"
            >
              <div className="font-medium text-sm flex items-center gap-2">
                {getTypeIcon(template.type)}
                {template.name}
              </div>
              <p className="text-xs text-muted-foreground mt-1">
                {template.description}
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* Command input */}
      {type === "command" && (
        <div className="space-y-2">
          <Label htmlFor="command-wizard">
            Command
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="ml-2 h-6 w-6 p-0"
              onClick={onToggleEnvHelp}
            >
              {showEnvHelp ? (
                <ChevronDown className="h-4 w-4" />
              ) : (
                <ChevronRight className="h-4 w-4" />
              )}
            </Button>
          </Label>
          <textarea
            id="command-wizard"
            value={command}
            onChange={(e) => onCommandChange(e.target.value)}
            rows={6}
            className="w-full px-3 py-2 border rounded-md font-mono text-sm"
            placeholder="echo 'Running tool: $CLAUDE_TOOL_NAME'"
          />
          {showEnvHelp && (
            <div className="bg-muted p-3 rounded text-sm space-y-2">
              <p className="font-medium flex items-center gap-2">
                <Info className="h-4 w-4" />
                Available Environment Variables:
              </p>
              {HOOK_ENV_VARS.map((env) => (
                <div key={env.name} className="ml-6">
                  <code className="bg-background px-2 py-1 rounded">
                    {env.name}
                  </code>
                  <span className="ml-2 text-muted-foreground">
                    - {env.description}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Prompt input (for both prompt and agent types) */}
      {(type === "prompt" || type === "agent") && (
        <div className="space-y-2">
          <Label htmlFor="prompt-wizard">Prompt</Label>
          <textarea
            id="prompt-wizard"
            value={prompt}
            onChange={(e) => onPromptChange(e.target.value)}
            rows={6}
            className="w-full px-3 py-2 border rounded-md text-sm"
            placeholder={type === "agent"
              ? "Instructions for the agent to execute..."
              : "Remember to follow security best practices..."}
          />
          <p className="text-sm text-muted-foreground">
            {type === "prompt" && "This prompt will be appended to Claude's context when the hook is triggered."}
            {type === "agent" && "This prompt will be sent to the subagent for processing."}
          </p>
        </div>
      )}

      {/* Model selector for agent type */}
      {type === "agent" && (
        <div className="space-y-2">
          <Label htmlFor="model-wizard">Agent Model</Label>
          <Select value={model} onValueChange={onModelChange}>
            <SelectTrigger id="model-wizard">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {AGENT_MODELS.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-sm text-muted-foreground">
            Choose which Claude model the agent should use.
          </p>
        </div>
      )}
    </div>
  );
}
