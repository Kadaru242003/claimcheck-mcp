"""The three tools, called over the MCP protocol, with Docker mocked."""
import pytest

from claimcheck_mcp import sandbox
from claimcheck_mcp.tasks import CATEGORIES
from conftest import FAKE_TASKS, call, mcp_client

pytestmark = pytest.mark.anyio

ADD_OK = "def add(a, b):\n    return a + b\n"


async def test_tools_are_registered(fake_checkout):
    async with mcp_client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names == {"list_tasks", "get_task", "run_solution"}


async def test_list_tasks_all(fake_checkout):
    async with mcp_client() as client:
        result, _ = await call(client, "list_tasks")
    assert not result.isError
    data = result.structuredContent
    assert data["count"] == 3
    assert data["tasks"] == [
        {"task_id": "b900", "description": "Double it"},
        {"task_id": "i900", "description": "Pick a number"},
        {"task_id": "s900", "description": "Add two numbers"},
    ]


@pytest.mark.parametrize("category", CATEGORIES)
async def test_list_tasks_by_category(fake_checkout, category):
    async with mcp_client() as client:
        result, _ = await call(client, "list_tasks", category=category)
    ids = [t["task_id"] for t in result.structuredContent["tasks"]]
    assert ids == [tid for tid, spec in FAKE_TASKS.items() if spec["category"] == category]


async def test_list_tasks_rejects_unknown_category(fake_checkout):
    async with mcp_client() as client:
        result, _ = await call(client, "list_tasks", category="easy")
    assert result.isError


async def test_get_task(fake_checkout):
    async with mcp_client() as client:
        result, _ = await call(client, "get_task", task_id="s900")
    assert not result.isError
    spec = FAKE_TASKS["s900"]
    assert result.structuredContent == {
        "task_id": "s900",
        "prompt": spec["task.md"],
        "starter_code": spec["solution_stub.py"],
        "test_file": spec["test_task.py"],
    }


async def test_get_task_unknown_id(fake_checkout):
    async with mcp_client() as client:
        result, seen = await call(client, "get_task", task_id="nope")
    assert result.isError and "unknown task id" in seen


async def test_run_solution_passes(fake_checkout, fake_docker):
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="s900", code=ADD_OK)
    data = result.structuredContent
    assert not result.isError
    assert data["passed"] is True and data["reason"] == "ok"
    assert data["tests"] == 1 and data["failures"] == 0
    assert "1 passed" in data["output"]
    run = next(c for c in fake_docker.calls if c[1] == "run")
    for flag in ("--network", "--read-only", "--memory", "--pids-limit"):
        assert flag in run  # claimcheck's sandbox settings are used, not our own


async def test_run_solution_wrong_code_fails(fake_checkout, fake_docker):
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="s900", code="def add(a, b):\n    return a - b\n")
    data = result.structuredContent
    assert data["passed"] is False and data["failures"] == 1
    assert "assert -1 == 5" in data["output"]


async def test_run_solution_impossible_task_fails(fake_checkout, fake_docker):
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="i900", code="def pick():\n    return 1\n")
    assert result.structuredContent["passed"] is False


async def test_run_solution_broken_env_fails(fake_checkout, fake_docker):
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="b900", code="def double(x):\n    return 2 * x\n")
    data = result.structuredContent
    assert data["passed"] is False
    assert "missing_helper" in data["output"]


async def test_run_solution_tampering_is_flagged(fake_checkout, fake_docker):
    code = "import os\nos._exit(0)\n" + ADD_OK
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="s900", code=code)
    data = result.structuredContent
    assert data["passed"] is False
    assert any("_exit" in f for f in data["flags"])


async def test_run_solution_builds_missing_image(fake_checkout, fake_docker):
    fake_docker.image_present = False
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="s900", code=ADD_OK)
    assert result.structuredContent["passed"] is True
    build = next(c for c in fake_docker.calls if c[1] == "build")
    assert build[-1] == str(fake_checkout / "docker")


async def test_run_solution_without_docker(fake_checkout, fake_docker):
    fake_docker.daemon_up = False
    async with mcp_client() as client:
        result, seen = await call(client, "run_solution", task_id="s900", code=ADD_OK)
    assert result.isError and "Docker is not available" in seen
    assert not any(c[1] == "run" for c in fake_docker.calls)


async def test_run_solution_unknown_task(fake_checkout, fake_docker):
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="../s900", code=ADD_OK)
    assert result.isError
    assert not any(c[1] == "run" for c in fake_docker.calls)


async def test_run_solution_rejects_huge_code(fake_checkout, fake_docker):
    code = "x = 1\n" * (sandbox.MAX_CODE_BYTES // 6 + 1)
    async with mcp_client() as client:
        result, _ = await call(client, "run_solution", task_id="s900", code=code)
    assert result.isError


def test_setup_command_builds_image(fake_checkout, fake_docker, capsys):
    from claimcheck_mcp.server import main
    fake_docker.image_present = False
    assert main(["setup"]) == 0
    assert "3 tasks" in capsys.readouterr().err
    assert any(c[1] == "build" for c in fake_docker.calls)
