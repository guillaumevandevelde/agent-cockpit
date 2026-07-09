# Subscriptions

Credentials for launching Claude Code sessions against alternate providers.

## Overview

The Subscriptions page manages the credentials that let the cockpit launch Claude Code sessions against providers other than Anthropic. Today that means one card: **MiniMax**, an alternate Anthropic-compatible API.

Each provider has its own card on the page showing the current configuration state (configured / not configured) and a form to enter or clear the API key. Keys are sent once to the backend, written to its local `.env` file, and never shown again — they're never stored in the database and never displayed back in the UI.

## How to Use

### Setting a MiniMax API Key

1. Click **"Set API Key"** on the MiniMax card
2. Paste the key into the password input
3. Click **Save**

The card flips to "API key configured" and the Change/Clear controls become available. Sessions launched against the MiniMax provider will use this key for authentication.

### Changing the Key

Click **Change** to overwrite the current key with a new one. The old key is replaced atomically — there's no overlap where both keys are valid.

### Clearing the Key

Click **Clear** to remove the key entirely. After clearing, the provider is no longer usable until a new key is set.

## Security Model

| Property | Behavior |
|----------|----------|
| Storage | Backend `.env` file only |
| Database | Never persisted in SQLite |
| Display | Never shown again after first save |
| Transport | HTTPS only (Cockpit binds localhost in dev; LAN deployments should be behind a reverse proxy) |
| Rotation | Change action overwrites; Clear action removes |

The plaintext key lives in the backend's `.env`. Only the existence of the key (boolean) is exposed via the API.

## Provider Status

The provider registry on the backend tracks each provider's configuration state:

- `configured` — `.env` has a non-empty key for this provider
- `not configured` — no key set; spawns against this provider will fail

The sidebar shows the same status per provider so you can see at a glance which providers are ready.

## Tips

- **Keys are per-backend, not per-project** — once set, every spawn against the provider uses the same key.
- **Restart the backend** if you change a key and an in-flight session is still using the old one — Claude Code caches credentials for the session lifetime.
- **For local dev**, set the key once and forget it; for shared environments, prefer the **Clear** action on logout.

## See also

- [Config](./config.md) — broader configuration management including providers
- [Sessions](./sessions.md) — sessions spawned against alternate providers appear here with their provider badge