import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MODAL_SIZES } from "@/lib/constants";
import { Button } from "@/components/ui/button";
import { type HookEvent, type HookType, type HookTemplate } from "@/types/hooks";
import { EventStep } from "./hook-wizard/EventStep";
import { MatcherStep } from "./hook-wizard/MatcherStep";
import { TypeConfigStep } from "./hook-wizard/TypeConfigStep";
import { AdvancedStep } from "./hook-wizard/AdvancedStep";

interface HookWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (hook: {
    event: HookEvent;
    matcher?: string;
    type: HookType;
    command?: string;
    prompt?: string;
    model?: string;
    async_?: boolean;
    statusMessage?: string;
    once?: boolean;
    timeout?: number;
    scope: "user" | "project";
  }) => Promise<void>;
}

export function HookWizard({ open, onOpenChange, onCreate }: HookWizardProps) {
  const [step, setStep] = useState(1);
  const [event, setEvent] = useState<HookEvent>("PreToolUse");
  const [matcher, setMatcher] = useState("");
  const [type, setType] = useState<HookType>("command");
  const [command, setCommand] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("haiku");
  const [asyncRun, setAsyncRun] = useState(false);
  const [statusMessage, setStatusMessage] = useState("");
  const [once, setOnce] = useState(false);
  const [timeout, setTimeout] = useState<number | undefined>(undefined);
  const [scope, setScope] = useState<"user" | "project">("user");
  const [creating, setCreating] = useState(false);
  const [showMatcherHelp, setShowMatcherHelp] = useState(false);
  const [showEnvHelp, setShowEnvHelp] = useState(false);

  const resetForm = () => {
    setStep(1);
    setEvent("PreToolUse");
    setMatcher("");
    setType("command");
    setCommand("");
    setPrompt("");
    setModel("haiku");
    setAsyncRun(false);
    setStatusMessage("");
    setOnce(false);
    setTimeout(undefined);
    setScope("user");
    setShowMatcherHelp(false);
    setShowEnvHelp(false);
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const hook: {
        event: HookEvent;
        matcher?: string;
        type: HookType;
        command?: string;
        prompt?: string;
        model?: string;
        async_?: boolean;
        statusMessage?: string;
        once?: boolean;
        timeout?: number;
        scope: "user" | "project";
      } = {
        event,
        matcher: matcher || undefined,
        type,
        scope,
        timeout,
      };

      if (type === "command") {
        hook.command = command;
      } else {
        hook.prompt = prompt;
        if (type === "agent") {
          hook.model = model;
        }
      }

      // Add optional fields
      if (asyncRun) hook.async_ = true;
      if (statusMessage) hook.statusMessage = statusMessage;
      if (once) hook.once = true;

      await onCreate(hook);
      resetForm();
      onOpenChange(false);
    } finally {
      setCreating(false);
    }
  };

  const applyTemplate = (template: HookTemplate) => {
    setEvent(template.event);
    setType(template.type);
    setMatcher(template.matcher || "");
    setCommand(template.command || "");
    setPrompt(template.prompt || "");
    setModel(template.model || "haiku");
    setAsyncRun(template.async_ || false);
    setStatusMessage(template.statusMessage || "");
    setOnce(template.once || false);
    setTimeout(template.timeout);
  };

  const canProceed = () => {
    if (step === 1) return true; // Event selection always valid
    if (step === 2) return true; // Matcher is optional
    if (step === 3) {
      if (type === "command") return command.trim() !== "";
      return prompt.trim() !== ""; // prompt and agent both need prompt
    }
    if (step === 4) return true; // Scope selection always valid
    return false;
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) resetForm();
      }}
    >
      <DialogContent className={MODAL_SIZES.LG}>
        <DialogHeader>
          <DialogTitle>Create New Hook</DialogTitle>
          <DialogDescription>
            Step {step} of 4 - Configure your Claude Code hook
          </DialogDescription>
        </DialogHeader>

        {/* Progress Bar */}
        <div className="w-full bg-muted h-2 rounded-full overflow-hidden">
          <div
            className="bg-primary h-full transition-all duration-300"
            style={{ width: `${(step / 4) * 100}%` }}
          />
        </div>

        <div className="space-y-6 py-4">
          {/* Step 1: Select Event Type */}
          {step === 1 && (
            <EventStep event={event} onEventChange={setEvent} />
          )}

          {/* Step 2: Configure Matcher */}
          {step === 2 && (
            <MatcherStep
              matcher={matcher}
              onMatcherChange={setMatcher}
              showMatcherHelp={showMatcherHelp}
              onToggleMatcherHelp={() => setShowMatcherHelp(!showMatcherHelp)}
            />
          )}

          {/* Step 3: Choose Type and Configure */}
          {step === 3 && (
            <TypeConfigStep
              type={type}
              onTypeChange={setType}
              command={command}
              onCommandChange={setCommand}
              prompt={prompt}
              onPromptChange={setPrompt}
              model={model}
              onModelChange={setModel}
              showEnvHelp={showEnvHelp}
              onToggleEnvHelp={() => setShowEnvHelp(!showEnvHelp)}
              onApplyTemplate={applyTemplate}
            />
          )}

          {/* Step 4: Advanced Options */}
          {step === 4 && (
            <AdvancedStep
              scope={scope}
              onScopeChange={setScope}
              asyncRun={asyncRun}
              onAsyncRunChange={setAsyncRun}
              once={once}
              onOnceChange={setOnce}
              statusMessage={statusMessage}
              onStatusMessageChange={setStatusMessage}
              timeout={timeout}
              onTimeoutChange={setTimeout}
              type={type}
              event={event}
              matcher={matcher}
              model={model}
            />
          )}
        </div>

        {/* Navigation */}
        <div className="flex gap-2">
          {step > 1 && (
            <Button
              variant="outline"
              onClick={() => setStep(step - 1)}
              disabled={creating}
            >
              Back
            </Button>
          )}
          {step < 4 ? (
            <Button
              onClick={() => setStep(step + 1)}
              disabled={!canProceed()}
              className="flex-1"
            >
              Next
            </Button>
          ) : (
            <Button
              onClick={handleCreate}
              disabled={creating || !canProceed()}
              className="flex-1"
            >
              {creating ? "Creating..." : "Create Hook"}
            </Button>
          )}
          <Button
            variant="outline"
            onClick={() => {
              resetForm();
              onOpenChange(false);
            }}
            disabled={creating}
          >
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
