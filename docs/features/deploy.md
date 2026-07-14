# Deploy

`DeployTarget` is the seam between "the agent built an app" and "the
app is in a registry somewhere a runner can pull from". The MVP
implements exactly one target — `GHCRDeployTarget` — which builds an
OCI image with `docker buildx build --push` and pushes it to
`ghcr.io`. A future `EcrDeployTarget`, `FlyDeployTarget`, etc. plug in
without changing the API or call sites.

**Scope (intentional, per the kanban card):** "deploy" means "the
image exists in the registry", **not** "a container is running
somewhere". Runtime provisioning (Fly machines, ECS tasks, K8s
rollouts, DNS, CDN) is a separate concern that lands in a follow-up
card once the registry-side MVP is proven.

## Overview

The interface lives in `app/services/deploy.py`:

```python
class DeployTarget(abc.ABC):
    id: str

    async def deploy(
        self, project_path: str, tag: str, *, credentials: dict[str, str] | None = None
    ) -> DeployResult: ...
```

`DeployResult` carries `status` (`pending | building | pushing |
completed | failed`), `image_ref`, `logs`, `started_at`,
`completed_at`, and (on failure) `error`. Implementations must never
raise — failures land on `status=FAILED` with `error` populated, so the
REST handler can serialise uniformly.

### REST endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/api/v1/deploy/targets`                  | List registered deploy targets |
| `POST` | `/api/v1/deploy/targets/{target_id}/invoke` | Run a deploy against `target_id` |

`invoke` blocks on the deploy itself (which can take minutes for a
large `docker buildx` run) and returns the full `DeployResult` in the
response body. A future streaming variant (WebSocket log tail) would
re-use the same `DeployTarget.deploy` coroutine without changing this
contract — see the docstring on `app.services.deploy`.

## GHCR target — quick start

```bash
# 1. Push a build via the REST API. The token is sourced from
#    `app.services.secrets_store` (PUT /api/v1/secrets) in production
#    scripts; here we inline it for clarity.
curl -X POST http://localhost:8000/api/v1/deploy/targets/ghcr/invoke \
  -H 'Content-Type: application/json' \
  -d '{
        "project_path": "/srv/repos/my-app",
        "tag": "v1.2.3",
        "credentials": {"ghcr_token": "ghp_..."}
      }'
# →
# {
#   "status": "completed",
#   "image_ref": "ghcr.io/acme/my-app:v1.2.3",
#   "logs": "...",
#   "started_at": "2026-07-14T12:00:00+00:00",
#   "completed_at": "2026-07-14T12:05:00+00:00",
#   "error": null
# }
```

The target infers `owner/repo` from `git remote get-url origin` of
the project tree, so the project must be a git repo with a GitHub
remote. Non-GitHub remotes are rejected up-front — `ghcr.io` is
GitHub-only, so failing fast beats a confusing 401 later.

### Credential resolution

The target looks up the GHCR token in this order:

1. `credentials.ghcr_token` in the request body — preferred for
   production callers that source the token from
   [`SecretStore`](secrets.md).
2. `gh auth token` — useful for a developer who has already run
   `gh auth login` and wants the platform to reuse that.
3. Otherwise, the deploy fails with a clear message that points the
   operator at both options.

The token is fed to `docker login ghcr.io --password-stdin`, never
via the command line — it never appears in `ps`/`argv`. Login
stdout/stderr are scrubbed of any echoed token before being
attached to the `DeployResult.logs`.

## Threat model

**What we're protecting**

The deploy path can:

* read project files (via `docker buildx` running in the project dir);
* make outbound network calls to `ghcr.io` and (transitively)
  `docker buildx` builders;
* spend CPU/disk on `docker buildx` (potentially a few minutes per
  push).

**What we're NOT protecting**

* Runtime isolation of the *built* container — that's the runtime
  layer's problem (Sandcastle, follow-up card), not the deploy
  layer's.
* Image vulnerability scanning — out of scope for the MVP. Add
  Trivy/Grype as a follow-up once the MVP ships.
* Supply-chain attacks via malicious Dockerfiles — out of scope for
  the MVP. A future card should pin base-image digests and refuse
  `:latest`.

**Trust assumptions**

1. The caller of `POST /api/v1/deploy/.../invoke` is authenticated
   and authorised to push to `ghcr.io/<owner>/<repo>`. (The MVP
   doesn't enforce this — it relies on the operator having set the
   `ghcr_token` to a PAT with `write:packages` for the target repo.
   Production deployments should add an auth layer on top of the
   REST endpoint.)
2. The token in `SecretStore` is at-rest encrypted
   (see [`docs/features/secrets.md`](secrets.md) for the
   scrypt + ChaCha20-Poly1305 details).
3. `git remote get-url origin` reflects the project owner's intent
   — i.e. an attacker can't rewrite `.git/config` to point at a
   different repo. (This is the project-local trust boundary; an
   attacker with write access to the project can already do
   everything, so this isn't a new escalation.)

**Audit log**

Every deploy emits two structured log lines via
`app.services.deploy._record_audit`:

* `deploy_start` — emitted after credential resolution, just before
  `docker buildx` runs. Carries `target_id`, `project_path`,
  `image_ref`, `status=building`.
* `deploy_complete` — emitted after `docker buildx` exits (success or
  failure). Carries the same fields plus `error` (on failure).

The hook is a structured log line today; the real `security_audit`
row write lands with follow-up #10. The contract is the same:
callable, never raises, **never logs the token** — only var *names*
(and in our case `image_ref`) are stable identifiers.

The token-scrubbing in `_docker_login` is a defence-in-depth: even
if `docker` echoes the password in some failure mode, the resulting
`logs` field won't contain it.

## Recovery / rollback

"Roll back a deploy" = "push an older tag to the same
`ghcr.io/<owner>/<repo>` image_ref, then point the runtime at it".
GHCR treats tags as mutable, so:

```bash
# 1. Re-tag the previous known-good image (already in the registry
#    under a different tag) as the tag the runtime reads.
docker buildx build --pull \
  --tag ghcr.io/acme/my-app:v1.2.3 \
  - <<EOF
FROM ghcr.io/acme/my-app:v1.2.2
EOF

# OR if you can re-push from local:
git checkout v1.2.2  # the commit that built v1.2.2
curl -X POST http://localhost:8000/api/v1/deploy/targets/ghcr/invoke \
  -H 'Content-Type: application/json' \
  -d '{"project_path": "/srv/repos/my-app", "tag": "v1.2.3"}'
```

Notes:

* **GHCR does NOT garbage-collect on tag overwrite.** When you push
  `v1.2.3` again, the previous image digest stays accessible via
  its `sha256:...` reference. Roll forward by tag, roll back by
  re-pushing — never rely on tag immutability.
* If the broken deploy was already wired up to a runtime
  (Sandcastle, ECS, K8s), the rollback needs to *also* re-point that
  runtime at the rebuilt tag. The deploy layer doesn't know about
  runtimes by design — that coordination is the orchestrator's job
  (see the kanban follow-up on runtime provisioning).
* The `logs` field of the failed deploy captures the failing
  `docker buildx` output; the audit log line carries the same
  `image_ref` and `error`. Together they're enough to reconstruct
  what went wrong without re-running the build.

## Out of scope (follow-ups)

* Runtime provisioning — "deploy" here means "image is in the
  registry". The orchestrator that pulls + runs the image lands as
  a separate card once the MVP is in production.
* Cloud-specific deploy providers (Fly, Vercel, ECS, GCR, ACR).
* DNS / domain wiring / CDN config.
* Cost governance (push quotas, image size budgets).
* Image vulnerability scanning (Trivy / Grype).
* Multi-architecture images (`docker buildx --platform`).