---
title: "MCP-config in worktree-transport — repo-root fallback"
type: decision
status: decided
---

# MCP-config in worktree-transport — repo-root fallback

**Datum:** 2026-07-31
**Status:** besloten
**Kaart:** `3672c0730b1b4b7ea31a52c414d17729` (kaart is intussen van het bord verwijderd; de hex-id is hier nog wel de canonieke verwijzing naar de oorspronkelijke beslissing — `decisions.md` Kaart-kolom houdt dezelfde id)
**Uitkomst:** **Route 2 verplicht:** gebruik de repo-root `--mcp-config`-fallback via `SpawnCommandOptions.repo_path`; route 1 (worktree-copy) is afgewezen omdat `Authorization: Bearer <api_token>` via git-recovery in de klant-repo-historie kan lekken.

> **Besluit:** kies **route 2** (repo-root `--mcp-config`-fallback via `SpawnCommandOptions.repo_path`); kies **niet route 1** (een `.mcp.json` naar de worktree kopiëren).

## Waarom route 2 verplicht is

Een worktree voor een extern productproject heeft niet noodzakelijk een eigen
Cockpit-configuratie. De spawn moet daarom de repo-root-configuratie aan Claude
Code meegeven via `--mcp-config`, terwijl de sessie in de worktree blijft werken.
`SpawnCommandOptions.repo_path` maakt die scheiding expliciet: de config wordt
opgelost vanuit de bron-repo, niet door bestanden in de klant-repo te materialiseren.

Route 1 (`_copy_repo_mcp_json_to_worktree`) is afgewezen. De gekopieerde config
bevat Cockpit's `Authorization: Bearer <api_token>`. Omdat de voorgeschreven
recovery voor een externe worktree `git add -A && git commit` kan gebruiken, kan
route 1 het Cockpit-token in de git-historie van de klant-repo vastleggen. Dat is
een security- en secret-hygiëne-fout, geen aanvaardbare fallback.

## Operationele regel

Bij ontbrekende MCP-config in de worktree:

1. zoek de config in de repo-root;
2. geef die locatie door als `--mcp-config` via `SpawnCommandOptions.repo_path`;
3. kopieer geen MCP-config naar de worktree;
4. behoud `--strict-mcp-config`, zodat globale MCP-configuratie van de host niet
   ongemerkt wordt geërfd.

Deze beslissing blijft gelden totdat een alternatief aantoonbaar geen credentials
kan materialiseren in de klant-repo, inclusief de voorgeschreven git-recovery.
