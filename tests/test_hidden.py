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


# Every id an agent might try in order to reach a hidden file. "{tasks}" is replaced with
# the absolute path of the tasks folder at run time.
BAD_TASK_IDS = {
    # path traversal
    "parent": "../s900",
    "parent-of-task": "s900/..",
    "round-trip": "s900/../s900",
    "dot-prefix": "./s900",
    "dotdot": "..",
    "dot": ".",
    "trailing-slash": "s900/",
    "into-reference": "s900/reference.py",
    "into-meta": "s900/meta.json",
    "traverse-to-reference": "i900/../s900/reference.py",
    "backslash": "s900\\reference.py",
    "deep-traversal": "../../../../etc/passwd",
    # absolute paths
    "abs-task": "{tasks}/s900",
    "abs-reference": "{tasks}/s900/reference.py",
    "abs-meta": "{tasks}/s900/meta.json",
    "abs-system": "/etc/passwd",
    # letter case (on a case-insensitive disk these would open the real folder)
    "upper": "S900",
    "upper-impossible": "I900",
    # near misses
    "empty": "",
    "space-after": "s900 ",
    "space-before": " s900",
    "newline": "s900\n",
    "null-byte": "s900\x00",
    "fullwidth": "\uff53900",
    "bare-reference": "reference.py",
    "bare-meta": "meta.json",
}


def _resolve(task_id, fake_checkout):
    return task_id.replace("{tasks}", str(fake_checkout / "tasks"))


@pytest.mark.parametrize("task_id", BAD_TASK_IDS.values(), ids=BAD_TASK_IDS.keys())
async def test_get_task_rejects_bad_ids(fake_checkout, task_id):
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id=_resolve(task_id, fake_checkout))
    assert result.isError
    assert "unknown task id" in seen
    assert SECRET not in seen


@pytest.mark.parametrize("task_id", BAD_TASK_IDS.values(), ids=BAD_TASK_IDS.keys())
async def test_run_solution_rejects_bad_ids(fake_checkout, fake_docker, task_id):
    async with mcp_client() as client:
        result, seen = await call(client, "run_solution", task_id=_resolve(task_id, fake_checkout), code="x = 1\n")
    assert result.isError
    assert "unknown task id" in seen
    assert SECRET not in seen
    assert not any(c[1] == "run" for c in fake_docker.calls)


# --- symlinks -------------------------------------------------------------------------

def _link(fake_checkout, name, target):
    """Replace tasks/s900/<name> with a symlink to target."""
    path = fake_checkout / "tasks" / "s900" / name
    if path.exists() or path.is_symlink():
        path.unlink()
    path.symlink_to(target)
    server._store_for.cache_clear()


def _outside_secret(tmp_path):
    secret = tmp_path / "outside" / "secret.txt"
    secret.parent.mkdir()
    secret.write_text(SECRET)
    return secret


@pytest.mark.parametrize("name,target", [
    ("task.md", "reference.py"),
    ("test_task.py", "meta.json"),
    ("solution_stub.py", "../i900/reference.py"),
])
async def test_get_task_refuses_visible_file_linked_to_ground_truth(fake_checkout, name, target):
    _link(fake_checkout, name, target)
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id="s900")
        listing, listed = await call(client, "list_tasks")
    assert result.isError
    assert SECRET not in seen
    assert not listing.isError and SECRET not in listed  # the listing still works


async def test_get_task_refuses_visible_file_linked_outside(fake_checkout, tmp_path):
    _link(fake_checkout, "task.md", _outside_secret(tmp_path))
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id="s900")
    assert result.isError and SECRET not in seen


async def test_get_task_refuses_link_to_sibling_case_variant(fake_checkout):
    task = fake_checkout / "tasks" / "s900"
    (task / "Reference.py").write_text(SECRET)
    _link(fake_checkout, "task.md", "Reference.py")
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id="s900")
    assert result.isError and SECRET not in seen


async def test_symlinked_task_folder_is_not_a_task(fake_checkout, fake_docker, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for name in ("task.md", "solution_stub.py", "test_task.py", "reference.py"):
        (elsewhere / name).write_text(SECRET)
    (elsewhere / "meta.json").write_text(json.dumps({"id": "s901", "category": "solvable", "solvable": True}))
    (fake_checkout / "tasks" / "s901").symlink_to(elsewhere)
    server._store_for.cache_clear()
    async with mcp_client() as client:
        listing, listed = await call(client, "list_tasks")
        got, seen_get = await call(client, "get_task", task_id="s901")
        ran, seen_run = await call(client, "run_solution", task_id="s901", code="x = 1\n")
    assert "s901" not in [t["task_id"] for t in listing.structuredContent["tasks"]]
    assert got.isError and ran.isError
    for seen in (listed, seen_get, seen_run):
        assert SECRET not in seen
    assert not any(c[1] == "run" for c in fake_docker.calls)


@pytest.mark.parametrize("name,target", [
    ("conftest.py", "reference.py"),  # claimcheck's copy follows links: this would mount the answer
    ("helper.py", "../i900/meta.json"),
    ("helpers", "../i900"),           # a linked folder
])
async def test_run_solution_refuses_task_with_symlink(fake_checkout, fake_docker, name, target):
    _link(fake_checkout, name, target)
    async with mcp_client() as client:
        result, seen = await call(client, "run_solution", task_id="s900", code="x = 1\n")
    assert result.isError and "symlink" in seen
    assert SECRET not in seen
    assert not any(c[1] == "run" for c in fake_docker.calls)


async def test_run_solution_refuses_link_outside(fake_checkout, fake_docker, tmp_path):
    _link(fake_checkout, "conftest.py", _outside_secret(tmp_path))
    async with mcp_client() as client:
        result, seen = await call(client, "run_solution", task_id="s900", code="x = 1\n")
    assert result.isError and SECRET not in seen
    assert not fake_docker.mounted_text


# --- letter case and nesting of the hidden files themselves -----------------------------

@pytest.mark.parametrize("name", ["Reference.py", "REFERENCE.PY", "Meta.json", "META.JSON"])
async def test_case_variant_ground_truth_is_never_shown_or_mounted(fake_checkout, fake_docker, name):
    (fake_checkout / "tasks" / "s900" / name).write_text(SECRET)
    server._store_for.cache_clear()
    async with mcp_client() as client:
        got, seen_get = await call(client, "get_task", task_id="s900")
        ran, seen_run = await call(client, "run_solution", task_id="s900", code="x = 1\n")
    assert not got.isError and SECRET not in seen_get  # get_task only reads its 3 files
    assert ran.isError and "ground-truth" in seen_run
    assert not fake_docker.mounted_text


async def test_nested_ground_truth_is_not_mounted(fake_checkout, fake_docker):
    sub = fake_checkout / "tasks" / "s900" / "data"
    sub.mkdir()
    (sub / "reference.py").write_text(SECRET)
    (sub / "meta.json").write_text(SECRET)
    async with mcp_client() as client:
        result, seen = await call(client, "run_solution", task_id="s900", code="def add(a, b):\n    return a + b\n")
    assert result.structuredContent["passed"] is True
    assert SECRET not in seen
    assert SECRET not in fake_docker.mounted_text[0]


def test_store_refuses_hidden_files_directly(fake_checkout):
    store = TaskStore(fake_checkout / "tasks")
    for name in HIDDEN_NAMES:
        with pytest.raises(PermissionError):
            store._read("s900", name)


def test_meta_fields_are_not_kept(fake_checkout):
    store = TaskStore(fake_checkout / "tasks")
    assert SECRET not in json.dumps(store.list()) + repr(vars(store))
