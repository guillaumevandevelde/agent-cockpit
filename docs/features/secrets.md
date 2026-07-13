# Secrets

Project-scoped, encrypted-at-rest secret storage for provider API keys, MCP bearer tokens, and similar sensitive values that callers (Agent Bridge, MCP server registration) need to retrieve at runtime.

## Overview

The Secrets store keeps one encrypted file per `project_key` under `~/.claude-registry/secrets/`. The plaintext inside each file is a JSON object `{name: value}`; on disk it is encrypted with scrypt-derived ChaCha20-Poly1305 under a single symmetric passphrase. Project keys containing `:` and `/` (e.g. `git:github.com/owner/repo`) are sanitized to a flat filename — no subdirectories are created.

The store's MVP is the `AGESecretStore` class in `app/services/secrets_store.py`. A future implementation can swap in HashiCorp Vault / AWS SM / Doppler without touching call sites — the `SecretStore` ABC is the contract.

The REST CRUD surface lives under `/api/v1/secrets/`:

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/?project_key=<key>` | List secret **names** for a project (no values) |
| `GET` | `/{project_key}/{name}` | Read a single secret (returns the value) |
| `PUT` | `/{project_key}/{name}` | Upsert a secret (idempotent) |
| `DELETE` | `/{project_key}/{name}` | Remove a secret |

The list endpoint takes `project_key` as a query parameter, not a path segment. Legitimate project keys contain `/` (e.g. `git:github.com/owner/repo`), and the `:path` converter on the singular routes greedily matches across slashes — using a query parameter sidesteps that ambiguity.

## How to Use

### Storing a secret

```bash
curl -X PUT http://localhost:8000/api/v1/secrets/git:github.com/owner/repo/MINIMAX_API_KEY \
  -H 'Content-Type: application/json' \
  -d '{"value":"sk-..."}'
```

Idempotent — repeated PUTs replace the previous value.

### Reading a secret

```bash
curl http://localhost:8000/api/v1/secrets/git:github.com/owner/repo/MINIMAX_API_KEY
# → {"name":"MINIMAX_API_KEY","value":"sk-..."}
```

This is the **only** endpoint that returns the value. The list endpoint returns names only.

### Listing names for a project

```bash
curl 'http://localhost:8000/api/v1/secrets/?project_key=git:github.com/owner/repo'
# → {"project_key":"git:github.com/owner/repo","names":["MINIMAX_API_KEY","GITHUB_TOKEN"]}
```

### Deleting a secret

```bash
curl -X DELETE http://localhost:8000/api/v1/secrets/git:github.com/owner/repo/MINIMAX_API_KEY
# → 204 No Content
```

## Secret names

Names must match `^[A-Za-z_][A-Za-z0-9_]{0,255}$` — env-var-style identifiers. This is enforced at the API layer; the filesystem sanitizer accepts a wider set, but rejecting `/` early prevents path-injection ambiguity between the project_key and name segments.

## Threat model

**In scope**

- **At-rest encryption.** Every secret file is encrypted under a passphrase before any disk write. A casual filesystem dump produces only ciphertext.
- **File-mode 0600.** Every write forces `chmod 0o600`. Other users on the host cannot read the file.
- **Passphrase hygiene.** The passphrase never lives on disk in plaintext. It comes from the `COCKPIT_SECRETS_PASSPHRASE` environment variable, or from the OS keyring under `claude-cockpit/secrets-passphrase`.
- **AAD binds the on-disk header.** Swapping any byte of the magic / scrypt cost / salt / nonce causes the AEAD tag check to fail. An attacker cannot reorder ciphertext between files without the store noticing.
- **Atomic writes.** Each write goes through a tempfile + `os.replace`, so a crash mid-write never leaves a half-written file readable to other processes.
- **Value never logged.** The store and the API only log secret **names**; the value is kept out of every log record by construction.

**Out of scope (explicit)**

- **An attacker with file + passphrase = game over.** This is the threat-model ceiling for the MVP. If an adversary has both the file and the passphrase, the design provides no further defense.
- **Hardware-backed key storage / HSM.** A future implementation can swap `AGESecretStore` for a Vault / AWS SM / Doppler backend that holds keys in a managed KMS — the `SecretStore` ABC is the seam.
- **Multi-tenant RBAC at the secret level.** All callers of the API see all secrets for a given project. If a Cockpit deployment needs per-agent secrets isolation, that lands in a separate store implementation (or an upstream auth layer).
- **Secret rotation / expiry.** There is no per-secret TTL. Operators rotate by PUTting a new value (and, if desired, DELETing the old name).
- **Per-name audit log.** The store logs upsert/read/delete with names but no values; if you need a tamper-evident audit trail, that lands in a separate stream.

## Recovery procedure

**There is no passphrase recovery.** If the passphrase is lost, every encrypted file in `~/.claude-registry/secrets/` is unrecoverable — the ciphertext is not decryptable without it. Operators who depend on Cockpit for credentialed work MUST back up the passphrase out-of-band (password manager, sealed envelope in a safe, whatever the ops handbook says).

If a file is lost:

1. There is no way to recover it from disk artifacts. Delete the stale `.age` file in `~/.claude-registry/secrets/`.
2. Re-enter each secret by hand via `PUT /api/v1/secrets/{project_key}/{name}` (or the planned UI on the Secrets page, future work).

If you want to rotate the passphrase (e.g. it was compromised):

1. Set the new passphrase in `COCKPIT_SECRETS_PASSPHRASE` (or update the keyring entry).
2. Restart the backend. **Reads against existing files will now fail with 503** — the on-disk ciphertext was encrypted under the old passphrase.
3. For each project: list names (which will also fail with 503), then re-PUT each secret. The store will encrypt each value under the new passphrase and replace the file. (A future operator-tool could automate this loop; not in scope here.)

## Operational notes

- **Default root.** `~/.claude-registry/secrets/`. Override by passing `root=` to `AGESecretStore` (the API's factory accepts an injected root in tests; production uses the default).
- **Per-project locking.** The store holds a `threading.Lock` per `project_key` so concurrent puts on the same project don't interleave read-modify-write. Async callers wrap with `asyncio.to_thread(store.put, ...)`.
- **scrypt cost.** Default `log2(N) = 20`, matching Filippo Valsorda's recommended cost for an interactive server. Tunable per-call (the cost is stored in the file header, so old files decrypt fine after the default changes).
- **On-disk format.** A leading 6-byte magic (`AGE1\x00\x00`, *not* the upstream `age` CLI's format — see the module docstring for why we don't reuse age's symmetric mode), then 1 byte of scrypt log2(N), 32 bytes salt, 12 bytes nonce, then ChaCha20-Poly1305 ciphertext. The AAD bound to the ciphertext is the entire 51-byte header.

## See also

- `backend/app/services/secrets_store.py` — the store and its on-disk format
- `backend/app/api/v1/secrets.py` — the REST routes
- `backend/tests/test_secrets_store.py` — store-level coverage (roundtrip, concurrency, file-mode, atomic-write, wrong-passphrase)
- `backend/tests/test_api_secrets.py` — API-level coverage (status codes, masking, error mapping)