"""API endpoints for memory management (CLAUDE.md, rules, etc.)."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.memory_service import MemoryService

router = APIRouter(prefix="/memory", tags=["Memory"])


# Request/Response schemas
class MemoryFileResponse(BaseModel):
    """Response for a memory file."""

    path: str
    exists: bool
    content: str | None = None
    imports: list[str] = []
    frontmatter: dict[str, Any] = {}
    error: str | None = None


class MemoryHierarchyItem(BaseModel):
    """Item in the memory hierarchy."""

    path: str
    scope: str
    type: str
    exists: bool
    readonly: bool
    description: str
    name: str | None = None
    relative_path: str | None = None


class MemoryHierarchyResponse(BaseModel):
    """Response for memory hierarchy."""

    files: list[MemoryHierarchyItem]


class RuleInfo(BaseModel):
    """Info about a rule file."""

    name: str
    path: str
    relative_path: str
    frontmatter: dict[str, Any] = {}
    scoped_paths: list[str] = []
    keywords: list[str] = []
    description: str = ""
    content_preview: str = ""


class ResolvedRuleInfo(RuleInfo):
    """Rule info as returned by /memory/rules/resolve, with the trigger labels
    that fired for the given context."""

    matched_triggers: list[str] = []


class RulesResolveRequest(BaseModel):
    """Request to resolve which rules apply to a given context."""

    prompt: str = ""
    touched_files: list[str] = []


class RulesResolveResponse(BaseModel):
    """Response from /memory/rules/resolve."""

    matched_rules: list[ResolvedRuleInfo] = []
    unmatched_rules: list[ResolvedRuleInfo] = []


# Aliases keep existing imports working if any caller pinned to the old shape
RuleMatch = ResolvedRuleInfo


class RulesListResponse(BaseModel):
    """Response for rules list."""

    rules: list[RuleInfo]
    rules_dir: str


class SaveMemoryRequest(BaseModel):
    """Request to save a memory file."""

    content: str


class SaveMemoryResponse(BaseModel):
    """Response for save operation."""

    success: bool
    path: str
    error: str | None = None


class CreateRuleRequest(BaseModel):
    """Request to create a new rule."""

    name: str
    content: str
    paths: list[str] | None = None
    keywords: list[str] | None = None
    description: str | None = None


class AutoMemoryFileInfo(BaseModel):
    """Info about an auto-memory file."""

    name: str
    path: str
    size: int
    modified_at: float


class AutoMemoryListResponse(BaseModel):
    """Response for auto-memory file listing."""

    memory_dir: str
    files: list[AutoMemoryFileInfo]


class ImportTreeNode(BaseModel):
    """Node in the import tree."""

    path: str
    exists: bool
    cycle: bool = False
    imports: list["ImportTreeNode"] = []
    error: str | None = None


# Make the self-reference work
ImportTreeNode.model_rebuild()


class ImportTreeResponse(BaseModel):
    """Response for import tree resolution."""

    tree: ImportTreeNode


# Endpoints


@router.get("/hierarchy", response_model=MemoryHierarchyResponse)
async def get_memory_hierarchy(
    project_path: str | None = Query(None, description="Project path"),
):
    """
    Get the memory file hierarchy.

    Returns all memory files (CLAUDE.md, rules) with their locations,
    scopes, and existence status.
    """
    files = MemoryService.get_memory_hierarchy(project_path)
    return MemoryHierarchyResponse(files=files)


@router.get("/file", response_model=MemoryFileResponse)
async def get_memory_file(
    file_path: str = Query(..., description="Absolute path to the memory file"),
    include_imports: bool = Query(True, description="Extract import references"),
):
    """
    Get a specific memory file with content and metadata.
    """
    result = MemoryService.get_memory_file(file_path, include_imports)
    return MemoryFileResponse(**result)


@router.put("/file", response_model=SaveMemoryResponse)
async def save_memory_file(
    file_path: str = Query(..., description="Absolute path to the memory file"),
    request: SaveMemoryRequest = ...,
):
    """
    Save content to a memory file.

    Creates the file and parent directories if they don't exist.
    Cannot modify the managed policy file (/etc/claude-code/CLAUDE.md).
    """
    result = MemoryService.save_memory_file(file_path, request.content)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Save failed"))

    return SaveMemoryResponse(**result)


@router.delete("/file", response_model=SaveMemoryResponse)
async def delete_memory_file(
    file_path: str = Query(..., description="Absolute path to the memory file"),
):
    """
    Delete a memory file.

    Cannot delete the managed policy file.
    """
    result = MemoryService.delete_memory_file(file_path)

    if not result["success"]:
        raise HTTPException(
            status_code=400, detail=result.get("error", "Delete failed")
        )

    return SaveMemoryResponse(**result)


@router.get("/rules", response_model=RulesListResponse)
async def list_rules(
    project_path: str | None = Query(None, description="Project path"),
):
    """
    List all rules in the .claude/rules/ directory.
    """
    from app.utils.path_utils import get_project_claude_dir

    rules = MemoryService.list_rules(project_path)
    rules_dir = get_project_claude_dir(project_path) / "rules"

    return RulesListResponse(rules=rules, rules_dir=str(rules_dir))


@router.post("/rules", response_model=SaveMemoryResponse)
async def create_rule(
    project_path: str | None = Query(None, description="Project path"),
    request: CreateRuleRequest = ...,
):
    """
    Create a new rule file in .claude/rules/.

    Triggers are declared via frontmatter:
    - ``paths``: glob list — rule applies when any touched file matches.
    - ``keywords``: keyword list — rule applies when the prompt contains
      any keyword (case-insensitive).
    A rule with neither trigger applies always.
    """
    result = MemoryService.create_rule(
        project_path=project_path,
        name=request.name,
        content=request.content,
        paths=request.paths,
        keywords=request.keywords,
        description=request.description,
    )

    if not result["success"]:
        raise HTTPException(
            status_code=400, detail=result.get("error", "Create failed")
        )

    return SaveMemoryResponse(**result)


@router.post("/rules/resolve", response_model=RulesResolveResponse)
async def resolve_rules(
    request: RulesResolveRequest,
    project_path: str = Query(..., description="Project path"),
):
    """
    Resolve which rules in ``.claude/rules/`` apply to the given agent
    context. A rule applies if its path-glob triggers match a file the agent
    touched, its keyword triggers appear in the prompt, or it has no
    triggers (always-on).

    Returns matched rules (with the trigger labels that fired) and
    unmatched rules in separate lists, so a UI can preview what would be
    injected for a hypothetical prompt.
    """
    result = MemoryService.resolve_applicable_rules(
        project_path=project_path,
        prompt=request.prompt,
        touched_files=request.touched_files,
    )
    return RulesResolveResponse(**result)


@router.get("/auto-memory", response_model=AutoMemoryListResponse)
async def list_auto_memory(
    project_path: str = Query(..., description="Absolute project path"),
):
    """
    List auto-memory files for a project.

    Returns .md files from ~/.claude/projects/<encoded-path>/memory/.
    """
    result = MemoryService.list_auto_memory(project_path)
    return AutoMemoryListResponse(**result)


@router.get("/imports", response_model=ImportTreeResponse)
async def resolve_imports(
    file_path: str = Query(..., description="Path to the memory file"),
):
    """
    Resolve the import tree for a memory file.

    Returns the full tree of @import references with cycle detection.
    """
    tree = MemoryService.resolve_imports(file_path)
    return ImportTreeResponse(tree=ImportTreeNode(**tree))
