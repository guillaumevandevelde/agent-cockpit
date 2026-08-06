---
name: security-triage
description: Use when draining or reviewing GitHub's security surfaces for a repo — the Dependabot alerts tab, the code-scanning (CodeQL/semgrep) alerts tab, or a pile of open dependabot PRs — including recurring sweeps and one-off questions like "how bad is our security backlog?" or "who should fix these alerts?". Do NOT use for auditing a specific diff for vulnerabilities (that's a code review) or for responding to a live incident.
---

# security-triage

GitHub's security tabs count **alerts**. You triage **causes**. A repo showing
243 code-scanning alerts usually has a dozen real questions in it, because one
CodeQL rule fires once per call site. Counting alerts produces a number nobody
can act on; grouping by rule produces a work plan.

The output of this skill is **one disposition per rule-group**, plus the cards
those dispositions imply. A list of individual alerts is not the output.

## When to use

- A recurring sweep over the Dependabot and code-scanning tabs is due.
- Someone asks how bad the security backlog is, or who should own it.
- Dependabot PRs have piled up and need a merge/close decision.

## When NOT to use

- Auditing one diff or PR for vulnerabilities → that's a code review.
- A live incident or a leaked credential → that's not triage, act directly.
- A single named CVE you already decided to fix → just fix it.

## The output contract

Whatever else you produce, produce this table. One row per **rule**, never per
alert:

| Rule / package | Sev | # | Disposition | Owner |
|---|---|---|---|---|

`Disposition` is exactly one of:

- **by-design** — the pattern is intentional for this app. Needs a recorded
  reason and a committed suppression, not a code change.
- **mechanical** — real but trivially fixable, no judgment needed.
- **real** — a genuine defect that needs thought.
- **noise** — the scanner should never have looked here (generated files,
  vendored code, build artefacts).

Every group gets a disposition before you file anything. A group you cannot
classify is itself the finding: say so and stop, rather than guessing.

## Step 1 — pull both surfaces

`gh` without `-R` reads the fork upstream and silently returns another repo's
data (CLAUDE.md, Gotchas). Always pass `-R`. Always quote URLs containing `?` —
unquoted, zsh treats it as a glob and the command never runs.

```bash
REPO=<owner>/<repo>

# Dependabot — group by package, not by alert
gh api --paginate "repos/$REPO/dependabot/alerts?state=open&per_page=100" \
  --jq '.[] | [.security_advisory.severity, .dependency.package.name,
               .dependency.manifest_path,
               .security_vulnerability.first_patched_version.identifier] | @tsv' \
  | sort | uniq -c | sort -rn

# Code scanning — group by rule, not by alert
gh api --paginate "repos/$REPO/code-scanning/alerts?state=open&per_page=100" \
  --jq '.[] | [(.rule.security_severity_level // "none"), .rule.id,
               .most_recent_instance.location.path] | @tsv' > /tmp/cs.tsv
cut -f1,2 /tmp/cs.tsv | sort | uniq -c | sort -rn      # the rule table
awk -F'\t' '$2=="<rule-id>"{print $3}' /tmp/cs.tsv | sort | uniq -c | sort -rn
```

`--paginate` is not optional. `per_page=100` silently caps at 100 without it,
and a 243-alert backlog reads as 100.

## Step 2 — decide reachability before severity

Scanner severity ignores your architecture. Decide reachability yourself; it
reorders the list more than severity does.

For a dependency, the question is which dependency tree it sits in:

```bash
cd frontend && npm ls <package>     # devDependency or shipped?
```

A `high` in a test-only or docs-build dependency outranks nothing. A `medium`
in shipped runtime code outranks most `high`s.

For a code-scanning rule, the question is whether the flagged input is actually
attacker-controlled in this deployment. A local single-user control panel that
reads paths the operator typed is not the same threat model as a public
multi-tenant service, and CodeQL's default suite assumes the latter.

## Step 3 — check for an existing PR before writing any fix

Dependabot has usually already opened the PR. Check before planning work:

```bash
gh pr list -R "$REPO" --author "app/dependabot" --limit 30
```

One PR often closes several alerts at once. Match PRs to alert groups and
report the count that a merge would clear — that number is usually most of the
backlog, and it changes who the work belongs to.

## Step 4 — record by-design decisions as committed policy

A **by-design** disposition is worthless if it lives only as a UI dismissal.
Nobody can review it, and the next scan re-raises it for a new call site.

Check which mode CodeQL runs in:

```bash
gh api "repos/$REPO/code-scanning/default-setup" --jq '{state, query_suite, languages}'
```

Under **default setup** there is no file in the repo where policy can live —
tuning a rule or excluding a path requires switching to advanced setup (a
committed `codeql.yml` plus a config with query filters or `paths-ignore`).
Under **advanced setup**, the config file is where a by-design decision belongs.

**Suppression comments are version-specific.** The legacy LGTM form
`# lgtm[rule-id]` is not the current CodeQL form `# codeql[rule-id]`. A stale
`lgtm[...]` comment reads as a handled alert while the alert stays open — check
the tab, not the comment, before believing a suppression works.

## Step 5 — file cards, one per disposition group

Split by owner, not by severity. The two halves go to different personas:

- **by-design + real** → one `work_type=analysis` card. The deliverable is a
  threat-model decision doc plus a `docs/cockpit/decisions.md` register line —
  not code. Bundle all by-design groups into this one card; they share a single
  underlying judgment.
- **mechanical + noise** → one `work_type=chore` card listing the concrete
  fixes, with file:line pointers.

Verify pointers before moving on — a dead `Where:` path sends every later
reader down a dead trail:

```bash
scripts/check-card-where-paths.sh --card=<new-card-id>
```

## Common mistakes

| Mistake | Why it hurts |
|---|---|
| Reporting the alert count | 243 alerts can be 8 questions. The count implies work that doesn't exist. |
| Omitting `--paginate` | Caps silently at 100; you triage a truncated backlog. |
| Omitting `-R` on `gh` | Reads the fork upstream, returns another repo's data, no error. |
| Sorting by severity | Scanner severity ignores reachability. A shipped `medium` beats a dev-only `high`. |
| Writing fixes before checking PRs | Dependabot usually already opened one that closes several alerts. |
| Dismissing in the UI only | Unreviewable, and it re-raises on the next call site. |
| Trusting an `lgtm[...]` comment | Legacy syntax. The alert is still open — check the tab. |
| One card per alert | Floods the board with rows that share one decision. |

## Red flags — stop

- You are about to dismiss an alert without writing down why.
- You are about to file a card whose body is a list of alert numbers.
- You classified a group as by-design because fixing it looked like a lot of work.

All three mean: go back to the rule-group table and write the disposition first.
