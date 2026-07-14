"""Service for managing Claude Code memory files (CLAUDE.md, rules, etc.).

Trigger model
-------------
Rules in ``.claude/rules/`` may declare triggers in their YAML frontmatter that
determine when they are injected into a session. Two trigger kinds are supported:

- ``paths`` (list or string) — a glob that must match one of the files the
  agent touched (e.g. ``backend/**/*.py``).
- ``keywords`` (list or string) — a keyword that must appear in the prompt
  (case-insensitive substring match).

A rule with neither trigger applies always (equivalent to a CLAUDE.md chunk).
A rule with one or more triggers applies when AT LEAST ONE of its triggers
matches (OR semantics). See ``resolve_applicable_rules`` for the resolver.
"""
import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

import yaml

from app.utils.path_utils import (
    convert_path_to_folder_name,
    get_claude_projects_dir,
    get_claude_user_config_dir,
    get_project_claude_dir,
)

logger = logging.getLogger(__name__)

class MemoryService:
    """Service for managing Claude Code memory files."""

    # Memory file types and their locations
    MEMORY_TYPES = {
        "managed": "/etc/claude-code/CLAUDE.md",  # Organization-wide (read-only)
        "user": "~/.claude/CLAUDE.md",  # Personal preferences (all projects)
        "project": "./CLAUDE.md",  # Team-shared project instructions
        "project_alt": "./.claude/CLAUDE.md",  # Alternative location
        "local": "./CLAUDE.local.md",  # Personal project-specific
    }

    @staticmethod
    def _get_user_claude_md() -> Path:
        """Get user-level CLAUDE.md path."""
        return get_claude_user_config_dir() / "CLAUDE.md"

    @staticmethod
    def _get_managed_claude_md() -> Path:
        """Get managed/org-level CLAUDE.md path."""
        return Path("/etc/claude-code/CLAUDE.md")

    @staticmethod
    def _get_project_claude_md(project_path: str | None) -> Path:
        """Get project-level CLAUDE.md path."""
        if project_path:
            return Path(project_path) / "CLAUDE.md"
        return Path.cwd() / "CLAUDE.md"

    @staticmethod
    def _get_project_alt_claude_md(project_path: str | None) -> Path:
        """Get alternative project-level CLAUDE.md path (.claude/CLAUDE.md)."""
        return get_project_claude_dir(project_path) / "CLAUDE.md"

    @staticmethod
    def _get_local_claude_md(project_path: str | None) -> Path:
        """Get local (personal project-specific) CLAUDE.md path."""
        if project_path:
            return Path(project_path) / "CLAUDE.local.md"
        return Path.cwd() / "CLAUDE.local.md"

    @staticmethod
    def _get_rules_dir(project_path: str | None) -> Path:
        """Get the .claude/rules/ directory path."""
        return get_project_claude_dir(project_path) / "rules"

    @staticmethod
    def _parse_rule_frontmatter(content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from a rule file.

        Returns:
            Tuple of (frontmatter dict, remaining content)
        """
        if not content.startswith("---"):
            return {}, content

        try:
            end_match = re.search(r"\n---\s*\n", content[3:])
            if not end_match:
                return {}, content

            frontmatter_str = content[3 : end_match.start() + 3]
            remaining = content[end_match.end() + 3 :]

            frontmatter = yaml.safe_load(frontmatter_str) or {}
            return frontmatter, remaining
        except yaml.YAMLError:
            return {}, content

    @classmethod
    def _coerce_str_list(cls, value: Any) -> list[str]:
        """Normalise a frontmatter value into a list of non-empty strings.

        Accepts a list (already), a single string (split on commas and newlines),
        or anything else (returned as ``[str(value)]`` if non-empty). Used for
        both ``paths`` and ``keywords`` frontmatter fields so authors can write
        ``keywords: deploy`` as well as ``keywords: [deploy, release]``.
        """
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _glob_match(pattern: str, candidate: str) -> bool:
        """Match ``candidate`` against a shell-style glob ``pattern``.

        ``fnmatch`` does not handle ``**`` specially (it treats ``*`` as "any
        characters except /"), which is good enough for repo-relative glob
        triggers like ``backend/**/*.py`` because the typical caller is a
        file path under a known project root. Paths are compared verbatim,
        so callers should pass project-relative or absolute consistently.
        """
        return fnmatch.fnmatch(candidate, pattern)

    @staticmethod
    def _keyword_match(keyword: str, prompt: str) -> bool:
        """Case-insensitive substring match of ``keyword`` in ``prompt``."""
        if not keyword or not prompt:
            return False
        return keyword.lower() in prompt.lower()

    @classmethod
    def _evaluate_rule(
        cls,
        rule: dict[str, Any],
        prompt: str,
        touched_files: list[str],
    ) -> list[str]:
        """Return the list of trigger labels that fired for ``rule``.

        Empty list means the rule did not match. A rule with no triggers
        (no ``paths`` and no ``keywords``) returns ``["always"]`` so it can
        be distinguished from rules whose triggers simply didn't match.
        """
        paths = rule.get("scoped_paths") or []
        keywords = rule.get("keywords") or []
        if not paths and not keywords:
            return ["always"]

        matched: list[str] = []
        for path_pattern in paths:
            if any(cls._glob_match(path_pattern, f) for f in touched_files):
                matched.append(f"path:{path_pattern}")
        for keyword in keywords:
            if cls._keyword_match(keyword, prompt):
                matched.append(f"keyword:{keyword}")
        return matched

    @staticmethod
    def _extract_imports(content: str) -> list[str]:
        """Extract @import references from content.

        Supports:
        - @path/to/file
        - @./relative/path
        - @~/user/path
        """
        # Match @path patterns (not inside code blocks)
        # Simple pattern: @followed by path characters
        pattern = r"@([~./]?[\w./-]+)"
        matches = re.findall(pattern, content)
        return list(set(matches))

    @classmethod
    def get_memory_hierarchy(
        cls, project_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Get the full memory file hierarchy.

        Args:
            project_path: Optional project directory path

        Returns:
            List of memory file info dicts with path, scope, exists, imports
        """
        files = []

        # Managed (organization-wide)
        managed_path = cls._get_managed_claude_md()
        files.append(
            {
                "path": str(managed_path),
                "scope": "managed",
                "type": "claude_md",
                "exists": managed_path.exists(),
                "readonly": True,
                "description": "Organization-wide instructions (managed policy)",
            }
        )

        # User-level
        user_path = cls._get_user_claude_md()
        files.append(
            {
                "path": str(user_path),
                "scope": "user",
                "type": "claude_md",
                "exists": user_path.exists(),
                "readonly": False,
                "description": "Personal preferences (all projects)",
            }
        )

        # Project-level
        project_path_obj = cls._get_project_claude_md(project_path)
        project_alt_path = cls._get_project_alt_claude_md(project_path)

        # Check which project path exists (prefer root CLAUDE.md)
        if project_path_obj.exists():
            files.append(
                {
                    "path": str(project_path_obj),
                    "scope": "project",
                    "type": "claude_md",
                    "exists": True,
                    "readonly": False,
                    "description": "Team-shared project instructions",
                }
            )
        elif project_alt_path.exists():
            files.append(
                {
                    "path": str(project_alt_path),
                    "scope": "project",
                    "type": "claude_md",
                    "exists": True,
                    "readonly": False,
                    "description": "Team-shared project instructions",
                }
            )
        else:
            # Show the preferred location even if it doesn't exist
            files.append(
                {
                    "path": str(project_path_obj),
                    "scope": "project",
                    "type": "claude_md",
                    "exists": False,
                    "readonly": False,
                    "description": "Team-shared project instructions",
                }
            )

        # Local (personal project-specific)
        local_path = cls._get_local_claude_md(project_path)
        files.append(
            {
                "path": str(local_path),
                "scope": "local",
                "type": "claude_md",
                "exists": local_path.exists(),
                "readonly": False,
                "description": "Personal project-specific preferences (gitignored)",
            }
        )

        # Rules directory
        rules_dir = cls._get_rules_dir(project_path)
        if rules_dir.exists():
            for rule_file in sorted(rules_dir.rglob("*.md")):
                rel_path = rule_file.relative_to(rules_dir)
                files.append(
                    {
                        "path": str(rule_file),
                        "scope": "rules",
                        "type": "rule",
                        "name": rel_path.stem,
                        "relative_path": str(rel_path),
                        "exists": True,
                        "readonly": False,
                        "description": f"Rule: {rel_path.stem}",
                    }
                )

        return files

    @classmethod
    def get_memory_file(
        cls, file_path: str, include_imports: bool = True
    ) -> dict[str, Any]:
        """Get a specific memory file with its content and metadata.

        Args:
            file_path: Absolute path to the memory file
            include_imports: Whether to extract and return import references

        Returns:
            Dict with path, content, exists, imports, frontmatter
        """
        path = Path(file_path).expanduser()

        result = {
            "path": str(path),
            "exists": path.exists(),
            "content": None,
            "imports": [],
            "frontmatter": {},
        }

        if path.exists():
            try:
                content = path.read_text(encoding="utf-8")
                result["content"] = content

                if include_imports:
                    result["imports"] = cls._extract_imports(content)

                # Parse frontmatter if it's a rule file
                if ".claude/rules/" in str(path) or "/rules/" in str(path):
                    frontmatter, _ = cls._parse_rule_frontmatter(content)
                    result["frontmatter"] = frontmatter

            except Exception as e:
                result["error"] = str(e)

        return result

    @classmethod
    def save_memory_file(
        cls, file_path: str, content: str, create_parents: bool = True
    ) -> dict[str, Any]:
        """Save content to a memory file.

        Args:
            file_path: Absolute path to the memory file
            content: Content to write
            create_parents: Whether to create parent directories

        Returns:
            Dict with success status and path
        """
        path = Path(file_path).expanduser()

        # Don't allow writing to managed path
        if str(path) == str(cls._get_managed_claude_md()):
            return {
                "success": False,
                "error": "Cannot modify managed policy file",
                "path": str(path),
            }

        try:
            if create_parents:
                path.parent.mkdir(parents=True, exist_ok=True)

            path.write_text(content, encoding="utf-8")

            return {
                "success": True,
                "path": str(path),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "path": str(path),
            }

    @classmethod
    def delete_memory_file(cls, file_path: str) -> dict[str, Any]:
        """Delete a memory file.

        Args:
            file_path: Absolute path to the memory file

        Returns:
            Dict with success status
        """
        path = Path(file_path).expanduser()

        # Don't allow deleting managed path
        if str(path) == str(cls._get_managed_claude_md()):
            return {
                "success": False,
                "error": "Cannot delete managed policy file",
                "path": str(path),
            }

        try:
            if path.exists():
                path.unlink()
                return {
                    "success": True,
                    "path": str(path),
                }
            else:
                return {
                    "success": False,
                    "error": "File does not exist",
                    "path": str(path),
                }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "path": str(path),
            }

    @classmethod
    def list_rules(cls, project_path: str | None = None) -> list[dict[str, Any]]:
        """List all rules in the .claude/rules/ directory.

        Args:
            project_path: Optional project directory path

        Returns:
            List of rule info dicts. Each carries ``scoped_paths`` (list of
            glob path triggers from the ``paths`` frontmatter) and
            ``keywords`` (list of keyword triggers from the ``keywords``
            frontmatter). Both are empty lists if no triggers were declared.
        """
        rules_dir = cls._get_rules_dir(project_path)
        rules = []

        if not rules_dir.exists():
            return rules

        for rule_file in sorted(rules_dir.rglob("*.md")):
            rel_path = rule_file.relative_to(rules_dir)
            content = rule_file.read_text(encoding="utf-8")
            frontmatter, body = cls._parse_rule_frontmatter(content)

            paths = cls._coerce_str_list(frontmatter.get("paths"))
            keywords = cls._coerce_str_list(frontmatter.get("keywords"))

            rules.append(
                {
                    "name": rel_path.stem,
                    "path": str(rule_file),
                    "relative_path": str(rel_path),
                    "frontmatter": frontmatter,
                    "scoped_paths": paths,
                    "keywords": keywords,
                    "description": frontmatter.get("description", ""),
                    "content_preview": body[:200] if body else "",
                }
            )

        return rules

    @classmethod
    def resolve_applicable_rules(
        cls,
        project_path: str,
        prompt: str,
        touched_files: list[str] | None = None,
    ) -> dict[str, Any]:
        """Resolve which rules apply to a given agent context.

        A rule applies if:
          - It has no triggers (no ``paths`` and no ``keywords``), OR
          - At least one of its path-glob triggers matches a file in
            ``touched_files``, OR
          - At least one of its keyword triggers appears in ``prompt``
            (case-insensitive substring match).

        Args:
            project_path: Absolute path to the project root.
            prompt: The current user/agent prompt text.
            touched_files: Optional list of file paths the agent has touched
                in the current session (typically repo-relative).

        Returns:
            Dict with:
              - ``matched_rules``: list of rule info dicts (same shape as
                ``list_rules``) augmented with a ``matched_triggers`` list
                naming the trigger labels that fired (e.g. ``"keyword:deploy"``,
                ``"path:backend/**/*.py"``, or ``["always"]``).
              - ``unmatched_rules``: rule info dicts for rules whose triggers
                did not fire.
        """
        rules = cls.list_rules(project_path)
        touched = touched_files or []
        matched: list[dict[str, Any]] = []
        unmatched: list[dict[str, Any]] = []

        for rule in rules:
            triggers = cls._evaluate_rule(rule, prompt or "", touched)
            rule_with_triggers = {**rule, "matched_triggers": triggers}
            if triggers:
                matched.append(rule_with_triggers)
            else:
                unmatched.append(rule_with_triggers)

        return {
            "matched_rules": matched,
            "unmatched_rules": unmatched,
        }

    @classmethod
    def create_rule(
        cls,
        project_path: str | None,
        name: str,
        content: str,
        paths: list[str] | None = None,
        keywords: list[str] | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Create a new rule file.

        Args:
            project_path: Project directory path
            name: Rule name (without .md extension)
            content: Rule content (markdown)
            paths: Optional list of glob path triggers — rule applies when
                any touched file matches any of these globs.
            keywords: Optional list of keyword triggers — rule applies when
                any keyword appears in the prompt (case-insensitive).
            description: Optional description for frontmatter

        Returns:
            Dict with success status and path
        """
        rules_dir = cls._get_rules_dir(project_path)
        rules_dir.mkdir(parents=True, exist_ok=True)

        rule_path = rules_dir / f"{name}.md"

        # Build frontmatter if we have metadata
        frontmatter_parts = []
        if description:
            frontmatter_parts.append(f"description: {description}")
        if paths:
            frontmatter_parts.append("paths:")
            for p in paths:
                frontmatter_parts.append(f"  - {p}")
        if keywords:
            frontmatter_parts.append("keywords:")
            for kw in keywords:
                frontmatter_parts.append(f"  - {kw}")

        if frontmatter_parts:
            full_content = "---\n" + "\n".join(frontmatter_parts) + "\n---\n\n" + content
        else:
            full_content = content

        return cls.save_memory_file(str(rule_path), full_content)

    @classmethod
    def list_auto_memory(cls, project_path: str) -> dict[str, Any]:
        """List auto-memory files for a project.

        Args:
            project_path: Absolute path to the project directory

        Returns:
            Dict with memory_dir and list of file info dicts
        """
        folder_name = convert_path_to_folder_name(project_path)
        memory_dir = get_claude_projects_dir() / folder_name / "memory"

        result: dict[str, Any] = {
            "memory_dir": str(memory_dir),
            "files": [],
        }

        if not memory_dir.exists():
            return result

        for md_file in sorted(memory_dir.glob("*.md")):
            stat = md_file.stat()
            result["files"].append(
                {
                    "name": md_file.name,
                    "path": str(md_file),
                    "size": stat.st_size,
                    "modified_at": stat.st_mtime,
                }
            )

        return result

    @classmethod
    def resolve_imports(
        cls, file_path: str, visited: set | None = None
    ) -> dict[str, Any]:
        """Resolve the import tree for a memory file.

        Args:
            file_path: Path to the memory file
            visited: Set of already-visited paths (for cycle detection)

        Returns:
            Dict with import tree structure
        """
        if visited is None:
            visited = set()

        path = Path(file_path).expanduser()
        path_str = str(path.resolve())

        if path_str in visited:
            return {
                "path": str(path),
                "exists": path.exists(),
                "cycle": True,
                "imports": [],
            }

        visited.add(path_str)

        result = {
            "path": str(path),
            "exists": path.exists(),
            "cycle": False,
            "imports": [],
        }

        if not path.exists():
            return result

        try:
            content = path.read_text(encoding="utf-8")
            imports = cls._extract_imports(content)

            for imp in imports:
                # Resolve import path
                if imp.startswith("~/"):
                    imp_path = Path(imp).expanduser()
                elif imp.startswith("./") or imp.startswith("../"):
                    imp_path = (path.parent / imp).resolve()
                else:
                    imp_path = Path(imp)

                # Recursively resolve
                imp_tree = cls.resolve_imports(str(imp_path), visited.copy())
                result["imports"].append(imp_tree)

        except Exception as e:
            result["error"] = str(e)

        return result
