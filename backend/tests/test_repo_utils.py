import subprocess

from app.utils.repo_utils import derive_repo_identity


def test_plain_directory_falls_back_to_realpath(tmp_path):
    ident = derive_repo_identity(str(tmp_path))
    assert ident["repo_root"] == str(tmp_path.resolve())
    assert ident["repo_name"] == tmp_path.name
    assert len(ident["repo_id"]) == 16


def test_git_worktrees_share_repo_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "f.txt").write_text("x")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    worktree = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-b", "wt-branch", str(worktree)],
        cwd=repo, check=True, capture_output=True,
    )

    assert derive_repo_identity(str(repo))["repo_id"] == derive_repo_identity(str(worktree))["repo_id"]
