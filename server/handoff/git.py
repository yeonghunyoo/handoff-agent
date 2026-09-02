"""git — 워크트리 · 브랜치 대조 · 머지. 본선에 쓰는 곳은 merge() 하나다.

역할별 브랜치 handoff/<role>, 워크트리 .handoff/worktrees/<role>. 워크트리 안에서는 아무것도 막지 않는다 —
본선으로 가는 길목(ship 승인 뒤 merge)에서 서버가 잰 것만 통과한다.
"""
import contextlib
import os
import subprocess

from . import util

PREFIX = "handoff/"
_cache = None
_WRITES = {"add", "rm", "commit", "merge", "reset", "checkout", "switch", "branch", "worktree",
           "stash", "init", "tag", "restore", "prune"}


class GitError(RuntimeError):
    pass


@contextlib.contextmanager
def cached():
    """도구 호출 하나 동안만 읽기를 재사용한다. 밖에서 난 커밋에 낡지 않게 호출 경계에서 버린다."""
    global _cache
    if _cache is not None:
        yield
        return
    _cache = {}
    try:
        yield
    finally:
        _cache = None


def _memo(key, produce):
    if _cache is None:
        return produce()
    if key not in _cache:
        _cache[key] = produce()
    return _cache[key]


def run(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", cwd, "-c", "core.quotePath=false", *args],
                       capture_output=True, text=True)
    if _cache is not None and args and args[0] in _WRITES:
        _cache.clear()
    if check and r.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:400]}")
    return r


def is_repo(root):
    return run(root, "rev-parse", "--git-dir", check=False).returncode == 0


def head(root):
    return run(root, "rev-parse", "HEAD").stdout.strip()


def has_commits(root):
    return run(root, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def branch(role):
    return PREFIX + role


def branch_exists(root, name):
    return _memo(("exists", root, name), lambda: run(
        root, "rev-parse", "--verify", "-q", name, check=False).returncode == 0)


def status_lines(cwd):
    out = run(cwd, "status", "--porcelain", "-uall").stdout
    return [(l[:2], l[3:].strip().strip('"')) for l in out.splitlines() if l.strip()]


def dirty_main(root):
    """본선 작업트리의 미커밋 변경 — 도구가 관리하는 자리는 뺀다."""
    managed = (util.HO_DIR + "/", util.DOCS_DIR + "/", ".gitignore", ".claude/", "CLAUDE.md")
    return [p for _, p in status_lines(root) if not p.startswith(managed)]


def ensure_worktree(root, role):
    wt = util.worktree(root, role)
    b = branch(role)
    if os.path.isdir(wt):
        return wt, False
    os.makedirs(os.path.dirname(wt), exist_ok=True)
    if branch_exists(root, b):
        run(root, "worktree", "add", wt, b)
    else:
        run(root, "worktree", "add", "-b", b, wt, "HEAD")
    return wt, True


def remove_worktrees(root, roles):
    for role in roles:
        wt = util.worktree(root, role)
        if os.path.isdir(wt):
            run(root, "worktree", "remove", "--force", wt, check=False)
        if branch_exists(root, branch(role)):
            run(root, "branch", "-D", branch(role), check=False)
    run(root, "worktree", "prune", check=False)


def merge_base(root, b):
    return _memo(("base", root, b), lambda: run(root, "merge-base", "HEAD", b).stdout.strip())


def changed_files(root, b):
    """브랜치가 본선 대비 바꾼 파일 (커밋 기준)."""
    def produce():
        base = merge_base(root, b)
        return [l.strip() for l in run(root, "diff", "--name-only", f"{base}..{b}").stdout.splitlines()
                if l.strip()]
    return list(_memo(("changed", root, b), produce))


def diff_text(root, b, *paths):
    def produce():
        base = merge_base(root, b)
        args = ["diff", "-U0", f"{base}..{b}"] + (["--", *paths] if paths else [])
        return run(root, *args).stdout
    return _memo(("diff", root, b, paths), produce)


def log(root, b):
    """[(sha, subject, [files])] — 브랜치 커밋만 (본선 제외), 오래된 것부터."""
    def produce():
        base = merge_base(root, b)
        out = run(root, "log", "--reverse", "--name-only", "--format=%x1e%h%x1f%s",
                  f"{base}..{b}").stdout
        commits = []
        for chunk in out.split("\x1e"):
            if not chunk.strip():
                continue
            headline, _, rest = chunk.partition("\n")
            sha, _, subject = headline.partition("\x1f")
            files = [l.strip() for l in rest.splitlines() if l.strip()]
            commits.append((sha, subject, files))
        return commits
    return list(_memo(("log", root, b), produce))


def commit_all(wt, message):
    run(wt, "add", "-A")
    if not run(wt, "status", "--porcelain").stdout.strip():
        return False
    run(wt, "commit", "-q", "-m", message)
    return True


def commit_paths(cwd, message, *paths):
    existing = [p for p in paths if os.path.exists(os.path.join(cwd, p))]
    if not existing:
        return False
    run(cwd, "add", "-A", "--", *existing)
    if run(cwd, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    run(cwd, "commit", "-q", "-m", message)
    return True


def merge(root, b, message):
    r = run(root, "merge", "--no-ff", "-m", message, b, check=False)
    if r.returncode != 0:
        run(root, "merge", "--abort", check=False)
        raise GitError(f"{b} 머지 충돌: {(r.stderr or r.stdout).strip()[:400]}")
    return head(root)
