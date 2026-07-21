/**
 * Provider/model co-validation helpers for the column-settings dialog.
 *
 * Kaart 1782fa43… (follow-up): when the user switches a column's
 * ``default_provider`` in the settings dialog, the model field must
 * follow — leaving an Anthropic model on a MiniMax column is exactly the
 * "stuck on opus" bug the product-owner decision wants to prevent. The
 * backend now also rejects such combinations (see
 * ``backend/app/api/v1/kanban/router.py`` update_column), but the
 * frontend should clear the field proactively so the user re-picks a
 * fitting model instead of getting a 422 toast on Save.
 *
 * Pure helper extracted so the rule is testable without the Radix
 * Select dropdown (which is brittle in jsdom — see the
 * ColumnSettingsDialog.test.tsx note around line 252). The dialog uses
 * this inside the provider Select's ``onValueChange``.
 */

/**
 * Decide what the model field should be when the user switches the
 * provider dropdown.
 *
 * Rules (kanban-card 1782fa43… product-owner decision):
 *  - if the current model is empty, leave it empty
 *  - if the new provider is unset (``null``), keep the model — no
 *    constraint applies
 *  - if the user is keeping the same provider, keep the model
 *  - if the current model is in the new provider's known-options list,
 *    keep it (no surprise data loss when the model happens to overlap)
 *  - otherwise clear the field, so the user re-picks a fitting model
 *    and Save can't sneak an incompatible combination past the
 *    backend's 422 guard
 */
export function modelForProviderChange(
  currentModel: string,
  oldProvider: string | null,
  newProvider: string | null,
  newProviderSuggestions: readonly string[],
): string {
  if (!currentModel) return currentModel;
  if (!newProvider) return currentModel;
  if (oldProvider === newProvider) return currentModel;
  if (newProviderSuggestions.includes(currentModel)) return currentModel;
  return "";
}
