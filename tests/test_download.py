"""Fetching tasks/ and docker/ from a GitHub-style archive (a local file:// URL here)."""
import tarfile

from claimcheck_mcp import tasks


def test_download_checkout_extracts_tasks_and_docker(fake_checkout, tmp_path):
    archive = tmp_path / "archive.tar.gz"
    top = f"claimcheck-{tasks.CLAIMCHECK_COMMIT}"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(fake_checkout / "tasks", arcname=f"{top}/tasks")
        tar.add(fake_checkout / "docker", arcname=f"{top}/docker")
        readme = tmp_path / "README.md"
        readme.write_text("x")
        tar.add(readme, arcname=f"{top}/README.md")
    dest = tmp_path / "cache" / "checkout"
    tasks.download_checkout(dest, url=archive.as_uri())
    assert (dest / "docker" / "Dockerfile").is_file()
    assert (dest / "tasks" / "s900" / "test_task.py").is_file()
    assert not (dest / "README.md").exists()
    assert [t["task_id"] for t in tasks.TaskStore(dest / "tasks").list()] == ["b900", "i900", "s900"]


def test_checkout_dir_uses_cache(fake_checkout, tmp_path, monkeypatch):
    monkeypatch.delenv("CLAIMCHECK_DIR")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    calls = []
    monkeypatch.setattr(tasks, "download_checkout", lambda dest: calls.append(dest) or dest.joinpath("tasks").mkdir(parents=True))
    first = tasks.checkout_dir()
    second = tasks.checkout_dir()
    assert first == second == tmp_path / "xdg" / "claimcheck-mcp" / tasks.CLAIMCHECK_COMMIT
    assert len(calls) == 1
