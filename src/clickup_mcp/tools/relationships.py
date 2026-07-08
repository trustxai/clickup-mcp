"""Task relationship tools — dependencies and task-to-task links (inventory G, v2).

Two relationship kinds, four tools:

- **Dependencies** (`clickup_add_dependency` / `clickup_delete_dependency`) model a
  *blocking* order: one task must finish before another can start. ``depends_on``
  and ``dependency_of`` are the two directions of the same edge.
- **Links** (`clickup_add_task_link` / `clickup_delete_task_link`) are a lightweight,
  non-blocking association between two tasks (a "related to" edge).

All four endpoints are v2 and accept the shared ``custom_task_ids`` / ``team_id``
pair so tasks can be referenced by their custom ID instead of the default ID.
"""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from clickup_mcp.client import get_client
from clickup_mcp.errors import handle_api_error
from clickup_mcp.server import mcp

_CONFIG = ConfigDict(str_strip_whitespace=True, extra="forbid")


class _CustomTaskIdParams(BaseModel):
    """Shared ``custom_task_ids`` / ``team_id`` pair for task-id resolution."""

    model_config = _CONFIG

    custom_task_ids: bool = Field(
        default=False,
        description="Set true to interpret task IDs as custom task IDs (e.g. 'PROJ-123'). Requires team_id.",
    )
    team_id: str | None = Field(
        default=None,
        description="Workspace (team) ID — required only when custom_task_ids is true.",
    )

    @model_validator(mode="after")
    def _require_team_id(self) -> _CustomTaskIdParams:
        if self.custom_task_ids and not self.team_id:
            raise ValueError(
                "team_id is required when custom_task_ids is true (e.g. custom_task_ids=true&team_id=123)."
            )
        return self

    def custom_id_query(self) -> dict[str, Any]:
        """Query fragment for custom-task-id resolution (empty when unused)."""
        if not self.custom_task_ids:
            return {}
        query: dict[str, Any] = {"custom_task_ids": "true"}
        if self.team_id:
            query["team_id"] = self.team_id
        return query


class AddDependencyInput(_CustomTaskIdParams):
    """Create a blocking dependency edge on ``task_id`` (exactly one direction)."""

    task_id: str = Field(..., min_length=1, description="The task that waits on or blocks the other task.")
    depends_on: str | None = Field(
        default=None,
        description="ID of a task that must finish BEFORE task_id (task_id waits on it). Mutually exclusive with dependency_of.",
    )
    dependency_of: str | None = Field(
        default=None,
        description="ID of a task that is waiting on task_id (task_id blocks it). Mutually exclusive with depends_on.",
    )

    @model_validator(mode="after")
    def _exactly_one_direction(self) -> AddDependencyInput:
        if bool(self.depends_on) == bool(self.dependency_of):
            raise ValueError("Provide exactly one of `depends_on` or `dependency_of` per request.")
        return self


class DeleteDependencyInput(_CustomTaskIdParams):
    """Remove a dependency edge from ``task_id`` (identify it by direction)."""

    task_id: str = Field(..., min_length=1, description="The task the dependency is being removed from.")
    depends_on: str | None = Field(
        default=None,
        description="ID of the task that task_id currently depends on (removes that edge). Mutually exclusive with dependency_of.",
    )
    dependency_of: str | None = Field(
        default=None,
        description="ID of the task that currently depends on task_id (removes that edge). Mutually exclusive with depends_on.",
    )

    @model_validator(mode="after")
    def _exactly_one_direction(self) -> DeleteDependencyInput:
        if bool(self.depends_on) == bool(self.dependency_of):
            raise ValueError("Provide exactly one of `depends_on` or `dependency_of` to identify the edge to remove.")
        return self


class TaskLinkInput(_CustomTaskIdParams):
    """Identify the two tasks of a link edge (used by add and delete)."""

    task_id: str = Field(..., min_length=1, description="The task to initiate the link from.")
    links_to: str = Field(..., min_length=1, description="The task to link to. Only task-to-task links are supported.")


@mcp.tool(
    name="clickup_add_dependency",
    annotations=ToolAnnotations(
        title="Add Task Dependency",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_add_dependency(params: AddDependencyInput) -> str:
    """Create a blocking dependency between two tasks.

    A dependency says one task must be completed before another. Pick the
    direction with exactly one of `depends_on` (task_id waits on that task) or
    `dependency_of` (task_id blocks that task) — the other end is `task_id`.

    When to Use:
    - To enforce an execution order ("finish design before build").
    - To surface blockers on the ClickUp task's Relationships tab.

    When NOT to Use:
    - For a loose "related to" association with no ordering — use
      `clickup_add_task_link` instead.
    - To remove an edge — use `clickup_delete_dependency`.

    Returns:
    A confirmation naming both tasks and the direction of the new edge.

    Examples:
    - task_id waits on another task:
      `params = {"task_id": "abc", "depends_on": "xyz"}`
    - task_id blocks another task (with custom IDs):
      `params = {"task_id": "PROJ-1", "dependency_of": "PROJ-2", "custom_task_ids": true, "team_id": "123"}`

    Error Handling:
    A 400/403 usually means the Dependencies ClickApp is disabled for the
    Workspace or the plan does not include it; a 404 means a task ID is wrong.
    """
    try:
        client = get_client()
        body: dict[str, Any] = (
            {"depends_on": params.depends_on} if params.depends_on else {"dependency_of": params.dependency_of}
        )
        await client.request(
            "POST",
            f"/task/{params.task_id}/dependency",
            json_body=body,
            params=params.custom_id_query() or None,
        )
        if params.depends_on:
            return (
                f"Added dependency: task **{params.task_id}** now depends on task "
                f"**{params.depends_on}** (that task must finish first)."
            )
        return (
            f"Added dependency: task **{params.dependency_of}** now depends on task "
            f"**{params.task_id}** (task {params.task_id} must finish first)."
        )
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_dependency",
    annotations=ToolAnnotations(
        title="Delete Task Dependency",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_dependency(params: DeleteDependencyInput) -> str:
    """Remove a blocking dependency edge between two tasks.

    Identify the edge to remove with exactly one of `depends_on` or
    `dependency_of` (the same direction you used to create it).

    When to Use:
    - To unblock a task once its dependency no longer applies.

    When NOT to Use:
    - To remove a non-blocking association — use `clickup_delete_task_link`.

    Returns:
    A confirmation that the dependency edge was removed.

    Examples:
    - `params = {"task_id": "abc", "depends_on": "xyz"}`

    Error Handling:
    A 404 means the edge (or a task ID) does not exist; the direction must match
    how the dependency was originally created.
    """
    try:
        client = get_client()
        query: dict[str, Any] = (
            {"depends_on": params.depends_on} if params.depends_on else {"dependency_of": params.dependency_of}
        )
        query.update(params.custom_id_query())
        await client.request("DELETE", f"/task/{params.task_id}/dependency", params=query)
        other = params.depends_on or params.dependency_of
        return f"Removed dependency between task **{params.task_id}** and task **{other}**."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_add_task_link",
    annotations=ToolAnnotations(
        title="Add Task Link",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    ),
)
async def clickup_add_task_link(params: TaskLinkInput) -> str:
    """Link two tasks with a non-blocking "related to" association.

    Unlike dependencies, a link implies no execution order — it just cross-
    references two tasks. Only task-to-task links are supported (not links to
    other object types or arbitrary URLs).

    When to Use:
    - To relate tasks that reference each other without blocking either one.

    When NOT to Use:
    - To enforce completion order — use `clickup_add_dependency`.

    Returns:
    A confirmation naming both linked tasks.

    Examples:
    - `params = {"task_id": "abc", "links_to": "xyz"}`

    Error Handling:
    A 404 means one of the task IDs is wrong; set custom_task_ids + team_id to
    reference tasks by custom ID.
    """
    try:
        client = get_client()
        await client.request(
            "POST",
            f"/task/{params.task_id}/link/{params.links_to}",
            params=params.custom_id_query() or None,
        )
        return f"Linked task **{params.task_id}** to task **{params.links_to}**."
    except Exception as exc:
        return handle_api_error(exc)


@mcp.tool(
    name="clickup_delete_task_link",
    annotations=ToolAnnotations(
        title="Delete Task Link",
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=True,
        openWorldHint=True,
    ),
)
async def clickup_delete_task_link(params: TaskLinkInput) -> str:
    """Remove a "related to" link between two tasks.

    When to Use:
    - To undo an association created with `clickup_add_task_link`.

    When NOT to Use:
    - To remove a blocking dependency — use `clickup_delete_dependency`.

    Returns:
    A confirmation that the link was removed.

    Examples:
    - `params = {"task_id": "abc", "links_to": "xyz"}`

    Error Handling:
    A 404 means the link (or a task ID) does not exist.
    """
    try:
        client = get_client()
        await client.request(
            "DELETE",
            f"/task/{params.task_id}/link/{params.links_to}",
            params=params.custom_id_query() or None,
        )
        return f"Removed link between task **{params.task_id}** and task **{params.links_to}**."
    except Exception as exc:
        return handle_api_error(exc)
