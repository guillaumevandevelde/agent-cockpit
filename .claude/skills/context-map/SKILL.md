---
name: context-map
description: 'Generate a map of all files relevant to a task before making changes'
---

# Context Map

Before implementing any changes, analyze the codebase and create a context map.

## Task

{{task_description}}

## Instructions

First, scan the task description for already-named files, functions, classes,
or test paths. If the description pins specific symbols, **read those files
directly** to gather what you need; only spawn an `Explore` subagent (or other
search) to fill remaining gaps — e.g. finding reference patterns, callers,
dependents, or tests you weren't given. Don't use full-repo fan-out to
re-derive facts already provided in the task.

Proceed with the steps below only when the task description does not already
name its targets, or to fill gaps after reading what it did name:

1. Search the codebase for files related to this task
2. Identify direct dependencies (imports/exports)
3. Find related tests
4. Look for similar patterns in existing code

## Output Format

```markdown
## Context Map

### Files to Modify
| File | Purpose | Changes Needed |
|------|---------|----------------|
| path/to/file | description | what changes |

### Dependencies (may need updates)
| File | Relationship |
|------|--------------|
| path/to/dep | imports X from modified file |

### Test Files
| Test | Coverage |
|------|----------|
| path/to/test | tests affected functionality |

### Reference Patterns
| File | Pattern |
|------|---------|
| path/to/similar | example to follow |

### Risk Assessment
- [ ] Breaking changes to public API
- [ ] Database migrations needed
- [ ] Configuration changes required
```

Do not proceed with implementation until this map is reviewed.
