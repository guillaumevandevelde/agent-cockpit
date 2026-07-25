# Updates

Run and monitor in-place upgrades of Agent Cockpit itself.

## Overview

The Updates page shows the current Cockpit version, branch, and commit, and provides a button to run the bundled update script. The update streams progress events over an SSE stream so you can watch each phase (preflight → pull → build → install → healthcheck → done) without refreshing.

The page is intentionally minimal: status at the top, one action button, and a live log underneath.

## How to Use

### Reading Status

The status card shows:

| Field | Meaning |
|-------|---------|
| `version` | Current Cockpit version |
| `commit` | Git HEAD commit hash |
| `branch` | Current branch |
| `update_script_available` | Whether the bundled `scripts/update.sh` is present |
| `working_tree_clean` | Whether the working tree has uncommitted changes |
| `update_possible` | Combined green-light for the update button |

If `update_possible` is `false`, the action button is disabled and the reason is shown (typically: dirty working tree, or missing update script).

### Running an Update

Click **"Update"** to start the update. The page streams events into the log:

| Event | Meaning |
|-------|---------|
| `preflight` | Sanity checks (deps, network, disk) |
| `pulling` | `git pull` against the current branch |
| `building` | Frontend build / backend reinstall |
| `installing` | Dependency installation (npm/pip) |
| `healthcheck` | Post-update smoke test |
| `done` | Update completed successfully |
| `error` | A phase failed — see the message for details |

A successful `done` event means the update is complete and the backend has been restarted. The page refreshes status after the run.

### Cancelling an Update

The active update exposes an abort control that cancels the in-flight request. The backend may have already passed the point of no return (e.g. already pulled new commits), so cancellation is best-effort.

## Update Script

The update is a thin wrapper around the bundled `scripts/update.sh`. The page does **not** run arbitrary commands — it shells out to this single script with no arguments. Update behavior is fully controlled by what the script does, so the script is the source of truth for "what does an update look like."

## Tips

- **Commit your changes first** — a dirty working tree blocks the update to avoid losing uncommitted work.
- **Read the log on errors** — the `error` event carries a human-readable reason; if it's transient (e.g. network), retrying often works.
- **Frontend rebuilds take a minute** — the `building` phase can be the longest; that's normal.
- **Backend restarts during the update** — sessions connected to the backend may briefly disconnect. Auto-reconnect on the client side handles this.

## See also

- [CHANGELOG.md](https://github.com/guillaumevandevelde/claude-cockpit/blob/master/CHANGELOG.md) — what changes between versions