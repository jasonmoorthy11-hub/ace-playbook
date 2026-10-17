import json
import subprocess

from ace_playbook.keys import normalize, resolve_key


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_normalize_lowercases_and_sanitizes():
    assert normalize("AcmeWidgets") == "acmewidgets"
    assert normalize("My Repo!") == "my-repo"
    assert normalize("a-b") == "a-b" and normalize("!!!") == "unknown"


def test_optional_separator_folding_merges_variants():
    names = ("AcmeWidgets", "acme-widgets", "acme_widgets")
    assert {normalize(n, fold_separators=True) for n in names} == {"acmewidgets"}
    assert len({normalize(n) for n in names}) == 3


def test_git_repo_and_subdir_share_key(tmp_path):
    repo = tmp_path / "Demo-App"
    (repo / "pkg" / "deep").mkdir(parents=True)
    git(repo, "init", "-q")
    assert resolve_key(repo) == resolve_key(repo / "pkg" / "deep") == "demo-app"


def test_worktree_resolves_to_main_repo(tmp_path):
    repo = tmp_path / "main-repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
        "--allow-empty", "-m", "i")
    wt = tmp_path / "elsewhere"
    git(repo, "worktree", "add", "-q", str(wt))
    assert resolve_key(wt) == "main-repo"


def test_non_git_falls_back_to_cwd_basename(tmp_path):
    d = tmp_path / "Plain Dir"
    d.mkdir()
    assert resolve_key(d) == "plain-dir"


def test_engagements_aliases_and_paths(tmp_path):
    eng = tmp_path / "engagements.json"
    work = tmp_path / "client-x" / "svc"
    work.mkdir(parents=True)
    other = tmp_path / "legacy-name"
    other.mkdir()
    eng.write_text(json.dumps({
        "paths": {str(tmp_path / "client-x"): "Client X"},
        "aliases": {"legacy-name": "modern"},
    }))
    assert resolve_key(work, eng) == "client-x"
    assert resolve_key(other, eng) == "modern"
    assert resolve_key(other, tmp_path / "missing.json") == "legacy-name"
