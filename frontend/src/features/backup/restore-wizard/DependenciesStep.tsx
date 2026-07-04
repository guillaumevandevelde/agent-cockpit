import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Switch } from "@/components/ui/switch";
import { AlertCircle, Check } from "lucide-react";
import { type RestorePlan, DEPENDENCY_KINDS } from "@/types/backup";

interface DependenciesStepProps {
  plan: RestorePlan | null;
  installDependencies: boolean;
  onInstallDependenciesChange: (v: boolean) => void;
}

export function DependenciesStep({
  plan,
  installDependencies,
  onInstallDependenciesChange,
}: DependenciesStepProps) {
  return (
    <div className="space-y-4">
      {plan?.has_dependencies ? (
        <>
          <p className="text-sm text-muted-foreground">
            The following dependencies need to be installed after restore:
          </p>

          <ScrollArea className="h-[200px] border rounded-lg p-3">
            <div className="space-y-2">
              {plan.dependencies.map((dep, i) => (
                <div
                  key={`${dep.kind}-${dep.name}-${i}`}
                  className="flex items-center gap-2 py-1"
                >
                  <Badge
                    variant="outline"
                    className={DEPENDENCY_KINDS[dep.kind]?.color || ""}
                  >
                    {DEPENDENCY_KINDS[dep.kind]?.label || dep.kind}
                  </Badge>
                  <span className="font-mono text-sm">{dep.name}</span>
                  {dep.version && (
                    <span className="text-muted-foreground text-sm">@{dep.version}</span>
                  )}
                  {dep.source && (
                    <span className="text-muted-foreground text-xs">from {dep.source}</span>
                  )}
                </div>
              ))}
            </div>
          </ScrollArea>

          <div className="flex items-center justify-between p-3 bg-muted rounded-lg">
            <div>
              <p className="font-medium">Auto-install dependencies</p>
              <p className="text-sm text-muted-foreground">
                Run npm/pip install after restore
              </p>
            </div>
            <Switch
              checked={installDependencies}
              onCheckedChange={onInstallDependenciesChange}
            />
          </div>

          {plan.manual_steps.length > 0 && (
            <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
              <h4 className="font-medium text-amber-800 flex items-center gap-2">
                <AlertCircle className="h-4 w-4" />
                Manual Steps Required
              </h4>
              <ul className="mt-2 text-sm text-amber-700 list-disc list-inside">
                {plan.manual_steps.map((step, i) => (
                  <li key={i}>{step}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <div className="text-center py-8">
          <Check className="h-12 w-12 text-green-500 mx-auto mb-4" />
          <h3 className="font-medium text-lg">No Dependencies Required</h3>
          <p className="text-muted-foreground mt-1">
            This backup contains only configuration files.
          </p>
        </div>
      )}
    </div>
  );
}
