"""Against the real claimcheck tasks and, when available, the real Docker sandbox.

Tasks are downloaded from the pinned claimcheck commit (or read from CLAIMCHECK_DIR).
Tests skip when the download or Docker is not available, unless CLAIMCHECK_MCP_STRICT=1
(set in CI), where a missing prerequisite is a failure instead.
"""
import json
import os

import pytest

from claimcheck_mcp import sandbox, server
from claimcheck_mcp.tasks import checkout_dir
from conftest import call, mcp_client

pytestmark = pytest.mark.integration
STRICT = os.environ.get("CLAIMCHECK_MCP_STRICT") == "1"


def _skip(reason):
    if STRICT:
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def real_checkout():
    server._store_for.cache_clear()
    try:
        return checkout_dir()
    except Exception as e:  # no network, etc.
        _skip(f"claimcheck tasks not available: {e}")


@pytest.fixture(scope="module")
def docker_ready(real_checkout):
    if not sandbox.docker_available():
        _skip("Docker is not available")
    sandbox.ensure_image(real_checkout / "docker")


def test_real_tasks_are_listed(real_checkout):
    store = server.store()
    assert len(store.list()) == 205
    assert {len(store.list(c)) for c in ("solvable", "impossible", "broken_env")} == {102, 54, 49}


def test_real_tasks_pass_the_sandbox_safety_check(real_checkout):
    """No real task has a symlink or a case-variant ground-truth file, so none is refused."""
    for task in server.store().list():
        sandbox.check_task_dir(server.store().task_dir(task["task_id"]))


def test_real_tasks_never_expose_ground_truth(real_checkout):
    """Every get_task result is free of every task's reference.py and meta.json content."""
    tasks = real_checkout / "tasks"
    store = server.store()
    listing = json.dumps(store.list())
    for task_dir in sorted(p for p in tasks.iterdir() if (p / "meta.json").exists()):
        shown = json.dumps(store.get(task_dir.name))
        assert "reference.py" not in shown and "meta.json" not in shown
        reference = (task_dir / "reference.py").read_text().strip()
        assert reference not in shown, task_dir.name
        meta = json.loads((task_dir / "meta.json").read_text())
        for key in ("why_unsolvable", "known_exploits"):
            for value in meta.get(key) or []:
                for secret in ([value] if isinstance(value, str) else []):
                    if len(secret) > 20:
                        assert secret not in shown and secret not in listing, task_dir.name
        if isinstance(meta.get("why_unsolvable"), str) and len(meta["why_unsolvable"]) > 20:
            assert meta["why_unsolvable"] not in shown and meta["why_unsolvable"] not in listing


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_run_solution_in_real_docker(real_checkout, docker_ready, anyio_backend):
    good = (
        "def common_prefix(words):\n"
        "    if not words:\n"
        "        return ''\n"
        "    p = words[0]\n"
        "    for w in words[1:]:\n"
        "        while not w.startswith(p):\n"
        "            p = p[:-1]\n"
        "    return p\n")
    async with mcp_client() as client:
        ok, _ = await call(client, "run_solution", task_id="s019", code=good)
        bad, _ = await call(client, "run_solution", task_id="s019", code="def common_prefix(words):\n    return ''\n")
        impossible, _ = await call(client, "run_solution", task_id="i001",
                                   code="def sort_numbers(xs):\n    return sorted(xs)\n")
    assert ok.structuredContent["passed"] is True, ok.structuredContent
    assert bad.structuredContent["passed"] is False
    assert impossible.structuredContent["passed"] is False
