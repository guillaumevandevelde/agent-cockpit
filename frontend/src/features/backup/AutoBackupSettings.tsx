import { useState, useEffect, useCallback } from "react";
import { Clock, Save, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { apiClient } from "@/lib/api";
import { toast } from "sonner";
import {
  type AutoBackupSettings as AutoBackupSettingsType,
  type AutoBackupRunResult,
  formatDate,
} from "@/types/backup";

interface AutoBackupSettingsProps {
  onChange?: () => void;
}

export function AutoBackupSettings({ onChange }: AutoBackupSettingsProps) {
  const [settings, setSettings] = useState<AutoBackupSettingsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const fetchSettings = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClient<AutoBackupSettingsType>(
        "/api/v1/backup/auto/settings"
      );
      setSettings(data);
    } catch {
      toast.error("Failed to load automatic backup settings");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const update = (patch: Partial<AutoBackupSettingsType>) => {
    setSettings((prev) => (prev ? { ...prev, ...patch } : prev));
  };

  const handleSave = async () => {
    if (!settings) return;
    setSaving(true);
    try {
      const saved = await apiClient<AutoBackupSettingsType>(
        "/api/v1/backup/auto/settings",
        {
          method: "PUT",
          body: JSON.stringify({
            enabled: settings.enabled,
            scope: settings.scope,
            time_of_day: settings.time_of_day,
            retention_days: settings.retention_days,
          }),
        }
      );
      setSettings(saved);
      toast.success("Automatic backup settings saved");
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to save settings"
      );
    } finally {
      setSaving(false);
    }
  };

  const handleRunNow = async () => {
    setRunning(true);
    try {
      const result = await apiClient<AutoBackupRunResult>(
        "/api/v1/backup/auto/run",
        { method: "POST" }
      );
      if (result.success) {
        toast.success("Automatic backup created");
        await fetchSettings();
        onChange?.();
      } else {
        toast.error(result.message || "Backup run failed");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to run backup");
    } finally {
      setRunning(false);
    }
  };

  if (loading || !settings) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5 text-primary" />
            Automatic Backups
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground">Loading...</div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Clock className="h-5 w-5 text-primary" />
          Automatic Backups
        </CardTitle>
        <CardDescription>
          Run a daily backup on a schedule and keep only the most recent days.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <Label htmlFor="auto-backup-enabled">Enabled</Label>
            <p className="text-sm text-muted-foreground">
              Create a user-config backup automatically every day.
            </p>
          </div>
          <Switch
            id="auto-backup-enabled"
            checked={settings.enabled}
            onCheckedChange={(checked) => update({ enabled: checked })}
          />
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-2">
            <Label htmlFor="auto-backup-time">Time of day</Label>
            <Input
              id="auto-backup-time"
              type="time"
              value={settings.time_of_day}
              onChange={(e) => update({ time_of_day: e.target.value })}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="auto-backup-scope">Scope</Label>
            <Select
              value={settings.scope}
              onValueChange={(value) =>
                update({ scope: value as AutoBackupSettingsType["scope"] })
              }
            >
              <SelectTrigger id="auto-backup-scope">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User config (~/.claude)</SelectItem>
                <SelectItem value="full">Full (user + project)</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="auto-backup-retention">Keep (days)</Label>
            <Input
              id="auto-backup-retention"
              type="number"
              min={1}
              value={settings.retention_days}
              onChange={(e) =>
                update({ retention_days: Number(e.target.value) })
              }
            />
          </div>
        </div>

        {settings.last_run_at && (
          <p className="text-sm text-muted-foreground">
            Last run: {formatDate(settings.last_run_at)}
            {settings.last_status ? ` — ${settings.last_status}` : ""}
          </p>
        )}

        <div className="flex gap-2">
          <Button onClick={handleSave} disabled={saving}>
            <Save className="h-4 w-4 mr-2" />
            {saving ? "Saving..." : "Save"}
          </Button>
          <Button
            variant="outline"
            onClick={handleRunNow}
            disabled={running || !settings.enabled}
          >
            <Play className="h-4 w-4 mr-2" />
            {running ? "Running..." : "Run now"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
