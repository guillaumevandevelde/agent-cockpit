import { useState, useEffect, useMemo, useCallback } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { MODAL_SIZES } from "@/lib/constants";
import { Progress } from "@/components/ui/progress";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Loader2,
  X,
} from "lucide-react";
import { apiClient } from "@/lib/api";
import {
  type Backup,
  type RestorePlan,
  type RestoreOptions,
  type RestoreResult,
  type DependencyInstallRequest,
  type DependencyInstallResult,
} from "@/types/backup";
import { SelectStep } from "./restore-wizard/SelectStep";
import { ContentsStep } from "./restore-wizard/ContentsStep";
import { ComponentsStep } from "./restore-wizard/ComponentsStep";
import { DependenciesStep } from "./restore-wizard/DependenciesStep";
import { ConfirmStep } from "./restore-wizard/ConfirmStep";
import { ResultsStep } from "./restore-wizard/ResultsStep";

interface RestoreWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backup: Backup | null;
  onRestoreComplete?: () => void;
  projectPath?: string;
}

const STEPS = [
  { title: "Select", description: "Review backup details" },
  { title: "Contents", description: "What's in the backup" },
  { title: "Components", description: "Choose what to restore" },
  { title: "Dependencies", description: "Required installations" },
  { title: "Confirm", description: "Review and restore" },
  { title: "Complete", description: "Restore results" },
];

export function RestoreWizard({
  open,
  onOpenChange,
  backup,
  onRestoreComplete,
  projectPath,
}: RestoreWizardProps) {
  const [step, setStep] = useState(0);
  const [plan, setPlan] = useState<RestorePlan | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [installingDeps, setInstallingDeps] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [restoreResult, setRestoreResult] = useState<RestoreResult | null>(null);
  const [depResult, setDepResult] = useState<DependencyInstallResult | null>(null);

  // Restore options
  const [selectedFiles, setSelectedFiles] = useState<Set<string>>(new Set());
  const [selectAll, setSelectAll] = useState(true);
  const [installDependencies, setInstallDependencies] = useState(true);
  const [skipPlugins, setSkipPlugins] = useState(false);
  const [skipSkills, setSkipSkills] = useState(false);
  const [dryRun, setDryRun] = useState(false);

  const fetchRestorePlan = useCallback(async () => {
    if (!backup) return;

    setLoadingPlan(true);
    setError(null);
    try {
      const url = projectPath
        ? `backup/${backup.id}/plan?project_path=${encodeURIComponent(projectPath)}`
        : `backup/${backup.id}/plan`;
      const response = await apiClient<RestorePlan>(url);
      setPlan(response);
    } catch {
      setError("Failed to load restore plan");
    } finally {
      setLoadingPlan(false);
    }
  }, [backup, projectPath]);

  useEffect(() => {
    if (open && backup) {
      fetchRestorePlan();
    }
  }, [open, backup, fetchRestorePlan]);

  useEffect(() => {
    // Initialize selected files when plan loads
    if (plan) {
      setSelectedFiles(new Set(plan.files_to_restore));
      setSelectAll(true);
    }
  }, [plan]);

  const resetForm = () => {
    setStep(0);
    setPlan(null);
    setError(null);
    setRestoreResult(null);
    setDepResult(null);
    setSelectedFiles(new Set());
    setSelectAll(true);
    setInstallDependencies(true);
    setSkipPlugins(false);
    setSkipSkills(false);
    setDryRun(false);
  };

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      resetForm();
    }
    onOpenChange(newOpen);
  };

  const handleNext = () => {
    if (step < STEPS.length - 1) {
      setStep(step + 1);
    }
  };

  const handleBack = () => {
    if (step > 0) {
      setStep(step - 1);
    }
  };

  const handleSelectAllFiles = (checked: boolean) => {
    setSelectAll(checked);
    if (checked && plan) {
      setSelectedFiles(new Set(plan.files_to_restore));
    } else {
      setSelectedFiles(new Set());
    }
  };

  const handleFileToggle = (file: string) => {
    const newSelected = new Set(selectedFiles);
    if (newSelected.has(file)) {
      newSelected.delete(file);
    } else {
      newSelected.add(file);
    }
    setSelectedFiles(newSelected);
    setSelectAll(plan ? newSelected.size === plan.files_to_restore.length : false);
  };

  const handleRestore = async () => {
    if (!backup) return;

    setRestoring(true);
    setError(null);
    try {
      const options: RestoreOptions = {
        selective_restore: selectAll ? undefined : Array.from(selectedFiles),
        install_dependencies: false, // We'll do this separately
        dry_run: dryRun,
        skip_plugins: skipPlugins,
        skip_skills: skipSkills,
      };

      const result = await apiClient<RestoreResult>(
        `backup/${backup.id}/restore`,
        {
          method: "POST",
          body: JSON.stringify(options),
        }
      );

      setRestoreResult(result);
      setStep(5); // Go to results step

      // Install dependencies if requested and not dry run
      if (installDependencies && !dryRun && plan?.has_dependencies) {
        await handleInstallDependencies();
      }

      onRestoreComplete?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to restore backup");
    } finally {
      setRestoring(false);
    }
  };

  const handleInstallDependencies = async () => {
    if (!backup) return;

    setInstallingDeps(true);
    try {
      const request: DependencyInstallRequest = {
        install_npm: true,
        install_pip: true,
        install_plugins: !skipPlugins,
      };

      const result = await apiClient<DependencyInstallResult>(
        `backup/${backup.id}/install-dependencies`,
        {
          method: "POST",
          body: JSON.stringify(request),
        }
      );

      setDepResult(result);
    } catch (err) {
      console.error("Dependency installation failed:", err);
    } finally {
      setInstallingDeps(false);
    }
  };

  // Group files by type
  const groupedFiles = useMemo(() => {
    if (!plan) return { skills: [], plugins: [], mcp: [], agents: [], commands: [], other: [] };

    const groups = {
      skills: [] as string[],
      plugins: [] as string[],
      mcp: [] as string[],
      agents: [] as string[],
      commands: [] as string[],
      other: [] as string[],
    };

    plan.files_to_restore.forEach((file) => {
      if (file.includes("/skills/") || file.includes("\\skills\\")) {
        groups.skills.push(file);
      } else if (file.includes("/plugins/") || file.includes("\\plugins\\")) {
        groups.plugins.push(file);
      } else if (file.includes("mcp") || file.includes(".mcp.json")) {
        groups.mcp.push(file);
      } else if (file.includes("/agents/") || file.includes("\\agents\\")) {
        groups.agents.push(file);
      } else if (file.includes("/commands/") || file.includes("\\commands\\")) {
        groups.commands.push(file);
      } else {
        groups.other.push(file);
      }
    });

    return groups;
  }, [plan]);

  const renderStepContent = () => {
    if (!backup) return null;

    if (loadingPlan) {
      return (
        <div className="flex flex-col items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-primary mb-4" />
          <p className="text-muted-foreground">Analyzing backup...</p>
        </div>
      );
    }

    switch (step) {
      // Step 0: Select/Review backup
      case 0:
        return <SelectStep backup={backup} plan={plan} />;

      // Step 1: Review contents
      case 1:
        return <ContentsStep plan={plan} groupedFiles={groupedFiles} />;

      // Step 2: Choose components
      case 2:
        return (
          <ComponentsStep
            plan={plan}
            selectAll={selectAll}
            selectedFiles={selectedFiles}
            skipSkills={skipSkills}
            skipPlugins={skipPlugins}
            onSelectAllFiles={handleSelectAllFiles}
            onFileToggle={handleFileToggle}
            onSkipSkillsChange={setSkipSkills}
            onSkipPluginsChange={setSkipPlugins}
          />
        );

      // Step 3: Dependencies
      case 3:
        return (
          <DependenciesStep
            plan={plan}
            installDependencies={installDependencies}
            onInstallDependenciesChange={setInstallDependencies}
          />
        );

      // Step 4: Confirm
      case 4:
        return (
          <ConfirmStep
            backup={backup}
            plan={plan}
            selectAll={selectAll}
            selectedFiles={selectedFiles}
            skipSkills={skipSkills}
            skipPlugins={skipPlugins}
            installDependencies={installDependencies}
            dryRun={dryRun}
            onDryRunChange={setDryRun}
          />
        );

      // Step 5: Results
      case 5:
        return (
          <ResultsStep
            restoreResult={restoreResult}
            installingDeps={installingDeps}
            depResult={depResult}
          />
        );

      default:
        return null;
    }
  };

  const canProceed = () => {
    switch (step) {
      case 0:
        return plan !== null && !loadingPlan;
      case 2:
        return selectAll || selectedFiles.size > 0;
      case 5:
        return true; // Results step - just close
      default:
        return true;
    }
  };

  const isLastStep = step === STEPS.length - 2; // Step 4 is confirm, step 5 is results
  const isResultsStep = step === STEPS.length - 1;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className={MODAL_SIZES.SM}>
        <DialogHeader>
          <DialogTitle>Restore Backup</DialogTitle>
          <DialogDescription>
            Step {step + 1} of {STEPS.length}: {STEPS[step].description}
          </DialogDescription>
        </DialogHeader>

        {!isResultsStep && (
          <div className="space-y-2">
            <Progress value={((step + 1) / STEPS.length) * 100} />
            <div className="flex justify-between text-xs text-muted-foreground">
              {STEPS.map((s, i) => (
                <span
                  key={i}
                  className={i <= step ? "text-primary font-medium" : ""}
                >
                  {s.title}
                </span>
              ))}
            </div>
          </div>
        )}

        {error && (
          <div className="text-sm text-destructive bg-destructive/10 p-3 rounded-md flex items-center gap-2">
            <AlertCircle className="h-4 w-4" />
            {error}
          </div>
        )}

        <div className="py-4 min-h-[300px]">{renderStepContent()}</div>

        <DialogFooter className="flex justify-between">
          {!isResultsStep ? (
            <>
              <Button variant="outline" onClick={handleBack} disabled={step === 0}>
                <ChevronLeft className="h-4 w-4 mr-2" />
                Back
              </Button>
              <div className="flex gap-2">
                <Button variant="outline" onClick={() => handleOpenChange(false)}>
                  Cancel
                </Button>
                {isLastStep ? (
                  <Button
                    variant={dryRun ? "secondary" : "destructive"}
                    onClick={handleRestore}
                    disabled={restoring || !canProceed()}
                  >
                    {restoring ? (
                      <>
                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                        Restoring...
                      </>
                    ) : dryRun ? (
                      "Preview Restore"
                    ) : (
                      "Restore Backup"
                    )}
                  </Button>
                ) : (
                  <Button onClick={handleNext} disabled={!canProceed()}>
                    Next
                    <ChevronRight className="h-4 w-4 ml-2" />
                  </Button>
                )}
              </div>
            </>
          ) : (
            <Button onClick={() => handleOpenChange(false)} className="ml-auto">
              <X className="h-4 w-4 mr-2" />
              Close
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
