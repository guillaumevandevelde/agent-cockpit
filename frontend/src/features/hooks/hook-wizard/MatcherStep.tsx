import { ChevronDown, ChevronRight, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MATCHER_EXAMPLES } from "@/types/hooks";

interface MatcherStepProps {
  matcher: string;
  onMatcherChange: (value: string) => void;
  showMatcherHelp: boolean;
  onToggleMatcherHelp: () => void;
}

export function MatcherStep({
  matcher,
  onMatcherChange,
  showMatcherHelp,
  onToggleMatcherHelp,
}: MatcherStepProps) {
  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-lg font-medium mb-2">
          Step 2: Configure Matcher (Optional)
        </h3>
        <p className="text-sm text-muted-foreground">
          Specify which tools or patterns this hook should match
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="matcher-wizard">
          Matcher Pattern
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-2 h-6 w-6 p-0"
            onClick={onToggleMatcherHelp}
          >
            {showMatcherHelp ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </Button>
        </Label>
        <Input
          id="matcher-wizard"
          value={matcher}
          onChange={(e) => onMatcherChange(e.target.value)}
          placeholder="Leave empty to match all tools"
        />
        {showMatcherHelp && (
          <div className="bg-muted p-3 rounded text-sm space-y-2">
            <p className="font-medium flex items-center gap-2">
              <Info className="h-4 w-4" />
              Pattern Examples:
            </p>
            {MATCHER_EXAMPLES.map((ex) => (
              <div key={ex.pattern} className="ml-6">
                <code className="bg-background px-2 py-1 rounded">
                  {ex.pattern}
                </code>
                <span className="ml-2 text-muted-foreground">
                  - {ex.description}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
