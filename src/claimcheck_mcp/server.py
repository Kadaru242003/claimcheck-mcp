"""claimcheck MCP server: list tasks, read a task, run a solution in the sandbox."""
from __future__ import annotations

import argparse
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

# pydantic requires typing_extensions.TypedDict on Python < 3.12.
from typing_extensions import TypedDict

from mcp.server.fastmcp import FastMCP

from . import sandbox
from .tasks import TaskStore, checkout_dir

mcp = FastMCP(
    "claimcheck",
    instructions=(
        "Tasks from the claimcheck benchmark. Use list_tasks to browse, get_task to read a "
        "task's prompt, starter code and tests, and run_solution to run your solution.py "
        "against the task's tests in a Docker sandbox."
    ),
)


class TaskSummary(TypedDict):
    task_id: str
    description: str


class TaskList(TypedDict):
    count: int
    tasks: list[TaskSummary]


class Task(TypedDict):
    task_id: str
    prompt: str
    starter_code: str
    test_file: str


class RunResult(TypedDict):
    task_id: str
    passed: bool
    output: str
    reason: str
    tests: int
    failures: int
    errors: int
    flags: list[str]


@lru_cache(maxsize=4)
def _store_for(root: Path) -> TaskStore:
    return TaskStore(root / "tasks")


def store() -> TaskStore:
    return _store_for(checkout_dir())


@mcp.tool()
def list_tasks(category: Literal["solvable", "impossible", "broken_env"] | None = None) -> TaskList:
    """List benchmark tasks with a one-line description each.

    category: optional filter, one of "solvable", "impossible", "broken_env".
    """
    tasks = store().list(category)
    return {"count": len(tasks), "tasks": tasks}


@mcp.tool()
def get_task(task_id: str) -> Task:
    """Get a task's prompt (task.md), starter code (solution_stub.py) and tests (test_task.py)."""
    return store().get(task_id)


@mcp.tool()
def run_solution(task_id: str, code: str) -> RunResult:
    """Run code as solution.py against the task's tests in claimcheck's Docker sandbox.

    Returns passed (true only if the static scan is clean and every test passed) and the
    pytest output.
    """
    task_dir = store().task_dir(task_id)
    result = sandbox.run_solution(task_dir, code, docker_context=checkout_dir() / "docker")
    return {"task_id": task_id, **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="claimcheck-mcp", description=__doc__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="run the MCP server over stdio (the default)")
    sub.add_parser("setup", help="download the tasks and build the Docker sandbox image")
    args = parser.parse_args(argv)

    if args.command == "setup":
        root = checkout_dir()
        print(f"tasks: {root / 'tasks'} ({len(store().list())} tasks)", file=sys.stderr)
        sandbox.ensure_image(root / "docker")
        print(f"sandbox image ready: {sandbox.grading.IMAGE}", file=sys.stderr)
        return 0
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
