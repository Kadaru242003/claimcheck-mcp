"""Run agent code with claimcheck's own grader, always in its Docker sandbox.

claimcheck.grading.grade copies the task without reference.py and meta.json, adds the
solution, runs pytest in a locked-down container (no network, read-only files, memory,
CPU and process limits, non-root user) and trusts only the JUnit report.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from claimcheck import grading

MAX_CODE_BYTES = 200_000
GROUND_TRUTH = {"reference.py", "meta.json"}


class DockerUnavailable(RuntimeError):
    pass


class UnsafeTask(RuntimeError):
    pass


def check_task_dir(task_dir: Path) -> None:
    """Refuse a task folder whose copy could carry ground truth into the container.

    grade() copies the folder with shutil.copytree, which follows symlinks, and skips only
    files named exactly reference.py and meta.json. So a symlink (say conftest.py ->
    reference.py) or a differently cased name (Reference.py) would get through.
    """
    task_dir = Path(task_dir)
    if task_dir.is_symlink():
        raise UnsafeTask(f"task folder {task_dir.name} is a symlink")
    for path in task_dir.rglob("*"):
        name = path.relative_to(task_dir).as_posix()
        if path.is_symlink():
            raise UnsafeTask(f"task file {name} is a symlink")
        if path.name.lower() in GROUND_TRUTH and path.name not in GROUND_TRUTH:
            raise UnsafeTask(f"task file {name} looks like a ground-truth file")


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def image_exists() -> bool:
    return subprocess.run(["docker", "image", "inspect", grading.IMAGE], capture_output=True).returncode == 0


def ensure_image(docker_context: Path) -> None:
    """Build claimcheck's runner image from its Dockerfile if it is missing.

    grading.build_image() expects a claimcheck checkout around the installed package, which
    a pip install does not have, so the same build is run against the downloaded checkout.
    """
    if not docker_available():
        raise DockerUnavailable("Docker is not available: install Docker and start the daemon")
    if image_exists():
        return
    proc = subprocess.run(["docker", "build", "-t", grading.IMAGE, str(docker_context)],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"building {grading.IMAGE} failed:\n{(proc.stdout + proc.stderr)[-2000:]}")


def run_solution(task_dir: Path, code: str, docker_context: Path, timeout: int = 30) -> dict:
    if not isinstance(code, str):
        raise ValueError("code must be a string")
    if len(code.encode()) > MAX_CODE_BYTES:
        raise ValueError(f"code is larger than {MAX_CODE_BYTES} bytes")
    check_task_dir(task_dir)
    ensure_image(docker_context)
    with tempfile.TemporaryDirectory() as tmp:
        solution = Path(tmp) / "solution.py"
        solution.write_text(code)
        result = grading.grade(task_dir, solution, timeout=timeout, backend="docker")
    return {"passed": result["passed"], "output": result["output"], "reason": result["reason"],
            "tests": result["tests"], "failures": result["failures"], "errors": result["errors"],
            "flags": result["flags"]}
