"""reference.py and meta.json are ground truth and must never reach the agent."""
import json

import pytest

from claimcheck_mcp import server
from claimcheck_mcp.tasks import TaskStore
from conftest import FAKE_TASKS, SECRET, call, mcp_client

pytestmark = pytest.mark.anyio

HIDDEN_NAMES = ("reference.py", "meta.json")


def assert_clean(seen: str):
    assert SECRET not in seen
    for name in HIDDEN_NAMES:
        assert name not in seen


async def test_no_tool_ever_returns_ground_truth(fake_checkout, fake_docker):
    async with mcp_client() as client:
        _, seen = await call(client, "list_tasks")
        assert_clean(seen)
        for category in ("solvable", "impossible", "broken_env"):
            _, seen = await call(client, "list_tasks", category=category)
            assert_clean(seen)
        for tid in FAKE_TASKS:
            result, seen = await call(client, "get_task", task_id=tid)
            assert not result.isError
            assert set(result.structuredContent) == {"task_id", "prompt", "starter_code", "test_file"}
            assert_clean(seen)
            result, seen = await call(client, "run_solution", task_id=tid, code="def f():\n    return 1\n")
            assert not result.isError
            assert_clean(seen)


async def test_sandbox_never_receives_hidden_files(fake_checkout, fake_docker):
    async with mcp_client() as client:
        for tid in FAKE_TASKS:
            await call(client, "run_solution", task_id=tid, code="x = 1\n")
    assert len(fake_docker.mounted_files) == len(FAKE_TASKS)
    for files in fake_docker.mounted_files:
        assert "solution.py" in files and "test_task.py" in files
        assert not set(files) & set(HIDDEN_NAMES)


async def test_solution_cannot_read_reference(fake_checkout, fake_docker):
    # Even code that tries to print the reference (and is flagged for it) sees nothing.
    code = ("import pathlib\n"
            "for p in pathlib.Path('/').glob('**/reference.py'):\n"
            "    print(p.read_text())\n")
    async with mcp_client() as client:
        _, seen = await call(client, "run_solution", task_id="s900", code=code)
    assert SECRET not in seen


@pytest.mark.parametrize("task_id", [
    "s900/reference.py", "s900/meta.json", "../s900", "s900/..", "", "reference.py", "meta.json",
])
async def test_get_task_rejects_paths(fake_checkout, task_id):
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id=task_id)
    assert result.isError
    assert SECRET not in seen


def test_store_refuses_hidden_files_directly(fake_checkout):
    store = TaskStore(fake_checkout / "tasks")
    for name in HIDDEN_NAMES:
        with pytest.raises(PermissionError):
            store._read("s900", name)


def test_store_refuses_symlinked_visible_file(fake_checkout):
    # A task.md that is really a link to reference.py must not be followed.
    task_md = fake_checkout / "tasks" / "s900" / "task.md"
    task_md.unlink()
    task_md.symlink_to(fake_checkout / "tasks" / "s900" / "reference.py")
    server._store_for.cache_clear()
    with pytest.raises(PermissionError):
        server.store().get("s900")


def test_meta_fields_are_not_kept(fake_checkout):
    store = TaskStore(fake_checkout / "tasks")
    assert SECRET not in json.dumps(store.list()) + repr(vars(store))
