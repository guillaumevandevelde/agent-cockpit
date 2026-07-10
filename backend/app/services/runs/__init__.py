"""Run services — tmux spawning, discovery, terminals, and grouping for live CLI runs.

A "Run" is a single running instance of an agentic CLI (a tmux session +
process tree, optionally grouped with peer runs in a RunGroup). See
``docs/cockpit/terminology.md`` for the canonical definition.
"""