"""Read-only access to claimcheck tasks, with ground truth kept hidden.

The claimcheck package installs only its Python code; the task folders and the sandbox
Dockerfile live next to it in the repository. They come from one of:

  CLAIMCHECK_DIR   a claimcheck checkout (must contain tasks/ and docker/)
  otherwise        the GitHub archive of CLAIMCHECK_COMMIT, downloaded once into
                   $XDG_CACHE_HOME/claimcheck-mcp/<commit> (default ~/.cache)

Only task.md, solution_stub.py and test_task.py are ever read for an agent.
reference.py and meta.json are ground truth: meta.json is read solely through
claimcheck's own loader to learn each task's category, and nothing from it is returned.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

from claimcheck.agent import HIDDEN
from claimcheck.plans import load_tasks

# Keep in step with the claimcheck pin in pyproject.toml.
CLAIMCHECK_COMMIT = "c6fb84c0d8a4011f0d70ddabb079ba1935283654"
ARCHIVE_URL = f"https://github.com/Kadaru242003/claimcheck/archive/{CLAIMCHECK_COMMIT}.tar.gz"

CATEGORIES = ("solvable", "impossible", "broken_env")

# The only files an agent may see, and the key each is returned under.
VISIBLE_FILES = {"task.md": "prompt", "solution_stub.py": "starter_code", "test_task.py": "test_file"}
assert not set(VISIBLE_FILES) & HIDDEN


class UnknownTask(ValueError):
    pass


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "claimcheck-mcp" / CLAIMCHECK_COMMIT


def download_checkout(dest: Path, url: str = ARCHIVE_URL) -> Path:
    """Fetch tasks/ and docker/ from the pinned claimcheck commit into dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        archive = Path(tmp) / "claimcheck.tar.gz"
        with urllib.request.urlopen(url, timeout=60) as resp, archive.open("wb") as f:
            shutil.copyfileobj(resp, f)
        staging = Path(tmp) / "checkout"
        with tarfile.open(archive) as tar:
            # GitHub archives have a single top-level folder: claimcheck-<commit>/
            members = [m for m in tar.getmembers()
                       if len(Path(m.name).parts) > 1 and Path(m.name).parts[1] in ("tasks", "docker")]
            for m in members:
                m.name = str(Path(*Path(m.name).parts[1:]))
            tar.extractall(staging, members=members, filter="data")
        if not (staging / "tasks").is_dir():
            raise RuntimeError(f"no tasks/ folder in {url}")
        try:
            staging.rename(dest)
        except OSError:
            if not (dest / "tasks").is_dir():  # lost a race with another process: fine
                raise
    return dest


def checkout_dir() -> Path:
    """Folder holding claimcheck's tasks/ and docker/."""
    configured = os.environ.get("CLAIMCHECK_DIR")
    if configured:
        root = Path(configured).expanduser().resolve()
        if not (root / "tasks").is_dir():
            raise RuntimeError(f"CLAIMCHECK_DIR={configured} has no tasks/ folder")
        return root
    root = _cache_dir()
    if not (root / "tasks").is_dir():
        download_checkout(root)
    return root


def _description(task_md: str) -> str:
    for line in task_md.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line
    return ""


class TaskStore:
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = Path(tasks_dir).resolve()
        # claimcheck's loader; only the category is kept, the rest of meta.json is dropped.
        self._category = {tid: meta["category"] for tid, meta in load_tasks(self.tasks_dir).items()}

    def task_dir(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or task_id not in self._category:
            raise UnknownTask(f"unknown task id: {task_id!r}")
        return self.tasks_dir / task_id

    def _read(self, task_id: str, name: str) -> str:
        if name not in VISIBLE_FILES:
            raise PermissionError(f"{name} is not visible to agents")
        folder = self.task_dir(task_id)
        path = (folder / name).resolve()
        # Refuse symlinks or anything else that would lead outside the task folder.
        if path.parent != folder or path.name in HIDDEN:
            raise PermissionError(f"{name} is not visible to agents")
        return path.read_text() if path.is_file() else ""

    def list(self, category: str | None = None) -> list[dict]:
        if category is not None and category not in CATEGORIES:
            raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
        return [{"task_id": tid, "description": _description(self._read(tid, "task.md"))}
                for tid, cat in sorted(self._category.items()) if category is None or cat == category]

    def get(self, task_id: str) -> dict:
        self.task_dir(task_id)
        return {"task_id": task_id, **{key: self._read(task_id, name) for name, key in VISIBLE_FILES.items()}}
