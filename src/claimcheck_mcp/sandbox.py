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


class DockerUnavailable(RuntimeError):
    pass


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
    ensure_image(docker_context)
    with tempfile.TemporaryDirectory() as tmp:
        solution = Path(tmp) / "solution.py"
        solution.write_text(code)
        result = grading.grade(task_dir, solution, timeout=timeout, backend="docker")
    return {"passed": result["passed"], "output": result["output"], "reason": result["reason"],
            "tests": result["tests"], "failures": result["failures"], "errors": result["errors"],
            "flags": result["flags"]}
