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

/**
 * Providers whose model list the backend enforces as a CLOSED set — an
 * unknown model is rejected with 422 on save.
 *
 * Mirrors ``_allowed_models_for_provider`` in
 * ``backend/app/api/v1/kanban/router.py``: it returns a list for these
 * providers and ``None`` (= accept anything) for every other one. Keep
 * the two in sync — if the backend starts validating a fourth provider,
 * add it here so the UI offers a picker instead of a free-text field that
 * can only produce a 422.
 */
export const CLOSED_MODEL_SET_PROVIDERS = [
  "anthropic",
  "minimax",
  "opencode-go",
  "opencode",
] as const;

/**
 * Whether the model field for this provider should be a closed picker
 * (dropdown) rather than free text.
 *
 * ``false`` for bedrock — AWS model ids are ARN-shaped
 * ("anthropic.claude-3-sonnet-20240229-v1:0"), never the bare aliases
 * the cli returns, so there is no list to pick from and the backend
 * accepts any string. ``false`` for an unset provider: the dispatch
 * chain resolves the provider later, so no constraint applies yet.
 */
export function hasClosedModelSet(provider: string | null | undefined): boolean {
  return (
    !!provider &&
    (CLOSED_MODEL_SET_PROVIDERS as readonly string[]).includes(provider)
  );
}

/**
 * Sanitise a column's STORED model when loading it into the edit form.
 *
 * ``modelForProviderChange`` only runs from the provider Select's
 * ``onValueChange``, so it never sees a column that was already persisted
 * in an invalid state. That gap is what kept the "minimax column shows no
 * models" bug alive after the co-validation fix: the live ``engineer``
 * column was stored as ``(minimax, opus)``, loaded into the form as
 * "opus", and because the model field is an ``<input list>`` whose
 * datalist is filtered by the current value, "opus" matched none of the
 * MiniMax options and the suggestion list rendered EMPTY. Saving then
 * failed with the backend's 422, so the column could not even be repaired
 * through the UI.
 *
 * Dropping an invalid value is safe: a null/empty model means "let the
 * dispatch chain choose", and for minimax that resolves to
 * ``MINIMAX_DEFAULT_MODEL`` (provider_env.py). Losing an unusable value
 * beats presenting it as valid.
 */
export function modelForProviderLoad(
  storedModel: string | null | undefined,
  provider: string | null | undefined,
  providerOptions: readonly string[],
): string {
  if (!storedModel) return "";
  if (!hasClosedModelSet(provider)) return storedModel;
  return providerOptions.includes(storedModel) ? storedModel : "";
}
