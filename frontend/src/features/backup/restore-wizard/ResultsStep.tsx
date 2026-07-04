import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { type RestoreResult, type DependencyInstallResult } from "@/types/backup";

interface ResultsStepProps {
  restoreResult: RestoreResult | null;
  installingDeps: boolean;
  depResult: DependencyInstallResult | null;
}

export function ResultsStep({ restoreResult, installingDeps, depResult }: ResultsStepProps) {
  return (
    <div className="space-y-4">
      {restoreResult && (
        <>
          <div
            className={`p-4 rounded-lg flex items-start gap-3 ${
              restoreResult.success
                ? "bg-green-50 border border-green-200"
                : "bg-red-50 border border-red-200"
            }`}
          >
            {restoreResult.success ? (
              <CheckCircle2 className="h-5 w-5 text-green-600 mt-0.5" />
            ) : (
              <XCircle className="h-5 w-5 text-red-600 mt-0.5" />
            )}
            <div>
              <h4 className="font-medium">
                {restoreResult.dry_run ? "Dry Run Complete" : "Restore Complete"}
              </h4>
              <p className="text-sm mt-1">{restoreResult.message}</p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="p-4 bg-muted rounded-lg text-center">
              <p className="text-2xl font-bold">{restoreResult.files_restored}</p>
              <p className="text-sm text-muted-foreground">
                Files {restoreResult.dry_run ? "would be" : ""} restored
              </p>
            </div>
            <div className="p-4 bg-muted rounded-lg text-center">
              <p className="text-2xl font-bold">{restoreResult.files_skipped}</p>
              <p className="text-sm text-muted-foreground">Files skipped</p>
            </div>
          </div>
        </>
      )}

      {installingDeps && (
        <div className="flex items-center justify-center gap-2 p-4">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Installing dependencies...</span>
        </div>
      )}

      {depResult && (
        <div className="space-y-2">
          <h4 className="font-medium">Dependency Installation</h4>
          <div className="space-y-1">
            {depResult.installed.map((dep, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <CheckCircle2 className="h-4 w-4 text-green-600" />
                <span>{dep.name}</span>
                <Badge variant="outline" className="text-xs">
                  {dep.kind}
                </Badge>
              </div>
            ))}
            {depResult.failed.map((dep, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <XCircle className="h-4 w-4 text-red-600" />
                <span>{dep.name}</span>
                <Badge variant="outline" className="text-xs">
                  {dep.kind}
                </Badge>
                <span className="text-muted-foreground">{dep.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {restoreResult?.manual_steps && restoreResult.manual_steps.length > 0 && (
        <div className="p-3 bg-amber-50 border border-amber-200 rounded-lg">
          <h4 className="font-medium text-amber-800">Manual Steps</h4>
          <ul className="mt-2 text-sm text-amber-700 list-disc list-inside">
            {restoreResult.manual_steps.map((step, i) => (
              <li key={i} className="font-mono text-xs">
                {step}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
