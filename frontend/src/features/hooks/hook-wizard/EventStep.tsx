import { Check } from "lucide-react";
import { HOOK_EVENTS, type HookEvent } from "@/types/hooks";

interface EventStepProps {
  event: HookEvent;
  onEventChange: (event: HookEvent) => void;
}

export function EventStep({ event, onEventChange }: EventStepProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium mb-2">
          Step 1: Select Event Type
        </h3>
        <p className="text-sm text-muted-foreground">
          Choose when your hook should be triggered
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {HOOK_EVENTS.map((e) => (
          <button
            key={e.name}
            onClick={() => onEventChange(e.name)}
            className={`p-4 border-2 rounded-lg text-left transition-all ${
              event === e.name
                ? "border-primary bg-primary/5"
                : "border-muted hover:border-primary/50"
            }`}
          >
            <div className="flex items-start justify-between">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-2xl">{e.icon}</span>
                  <span className="font-medium">{e.label}</span>
                </div>
                <p className="text-sm text-muted-foreground">
                  {e.description}
                </p>
              </div>
              {event === e.name && (
                <Check className="h-5 w-5 text-primary flex-shrink-0 ml-2" />
              )}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
