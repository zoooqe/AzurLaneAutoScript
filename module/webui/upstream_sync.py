"""Follow upstream automatically when Alas starts.

Alas ships its own updater, but it only knows one move: `git reset --hard
origin/master`, which silently throws away every local change. This checkout
carries island improvements that upstream does not have, so instead of resetting
we merge.

The merge is only allowed to resolve conflicts it fully understands:

* island transport files follow upstream, since PR #6070 made the local port
  redundant and upstream keeps improving it;
* island assets are merged name by name, so local templates survive while
  upstream definitions win;
* i18n conflicts take the upstream wording, then the local keys that belong to
  this branch are verified to still be there;
* `gui.py` and `module/webui/updater.py` keep the local side of the conflicting
  hunks, since they are this branch's own hook into the update flow.

Anything else stops the merge, leaves the checkout untouched and reports the
file list. Startup must never end up with a half-merged tree.
"""
import json
import os
import re
import subprocess
import sys

import yaml

from module.logger import logger

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
UPSTREAM = "origin"

# Files owned by upstream from now on. The local copies were a port of PR #6070,
# which upstream merged, so there is nothing left here worth keeping.
THEIRS = {
    "module/island/transport.py",
    "module/island_handler/transport_optimizer.py",
    "module/island_handler/highs.py",
    "module/island_handler/milp.py",
    "tests/island_handler/test_transport_optimizer.py",
    "tests/island_handler/production_plan_calculator/test_transport_solver.py",
}

# Same, for whole directories: transport UI templates and item icons.
THEIRS_PREFIX = (
    "assets/island/transport/",
    "assets/cn/island/ISLAND_TRANSPORT",
    "assets/jp/island/ISLAND_TRANSPORT",
    "assets/en/island/ISLAND_TRANSPORT",
    "assets/tw/island/ISLAND_TRANSPORT",
    "assets/cn/island/TEMPLATE_ISLAND_TRANSPORT",
    "assets/jp/island/TEMPLATE_ISLAND_TRANSPORT",
    "assets/en/island/TEMPLATE_ISLAND_TRANSPORT",
    "assets/tw/island/TEMPLATE_ISLAND_TRANSPORT",
    "tests/island/transport/",
)

# Merged definition by definition instead of whole file.
ASSETS_FILE = "module/island/assets.py"

# Conflict blocks keep the local side, the rest of the file follows upstream.
# These are this branch's hooks into the update flow: `gui.py` starts the sync,
# `updater.py` makes the update button merge instead of reset. Upstream touching
# them as well must not stop the sync, or the "no manual work" promise breaks the
# first time upstream cleans up its updater.
OURS = {
    "module/webui/updater.py",
    "gui.py",
}

I18N_FILES = (
    "module/config/i18n/zh-CN.json",
    "module/config/i18n/zh-TW.json",
    "module/config/i18n/en-US.json",
    "module/config/i18n/ja-JP.json",
)

# Local tests that upstream replaced with differently named files. Git cannot
# see that they test the same thing, so without this they survive the merge and
# then fail to import the rewritten upstream API.
OBSOLETE_FILES = {
    # Replaced by tests/island/transport/* once upstream shipped PR #6070
    "tests/island/test_transport.py",
    # Its cases moved into test_production_planner.py and test_production_problem.py
    "tests/island_handler/production_plan_calculator/test_transport_solver.py",
}

# i18n keys this branch owns. A merge that loses one of them is a bug in the
# conflict policy, not an upstream change, so the merge is rolled back.
KEEP_I18N_KEYS = (
    "IslandProduction.BedtimeEnable",
    "IslandProduction.BedtimeTime",
    "IslandProduction.BedtimeHours",
    "Gui.Text.IslandItemsPick",
    "Gui.Text.IslandItemsAmount",
    "Gui.Text.IslandItemsInvalidItem",
)

# Import must still work after the merge, otherwise the next start is broken.
SMOKE_IMPORTS = (
    "module.island.assets",
    "module.island.transport",
    "module.island.collect",
    "module.island_handler.restaurant",
    "module.island_handler.transport_optimizer",
    "module.ui.ui",
)

CONFLICT_RE = re.compile(
    r"<<<<<<< HEAD\r?\n(.*?)=======\r?\n(.*?)>>>>>>> [^\n]*\r?\n", re.S
)
ASSET_DEF_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*=\s*(Button|Template)\(")


class SyncResult:
    def __init__(self, status, detail="", files=None):
        self.status = status
        self.detail = detail
        self.files = files or []

    def __repr__(self):
        return "SyncResult(%s, %r, %d files)" % (self.status, self.detail, len(self.files))


def read_deploy_config(path=None):
    """Read the bits of deploy.yaml this module needs, without DeployConfig.

    DeployConfig is an upstream class; pulling it in would tie the sync to its
    import chain, and a missing attribute there must not break startup.
    """
    path = path or os.path.join(ROOT, "config", "deploy.yaml")
    data = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("Upstream sync: cannot read %s: %s" % (path, e))
            data = {}
    deploy = data.get("Deploy") or {}
    git = deploy.get("Git") or {}
    executable = git.get("GitExecutable") or "git"
    if not os.path.isabs(executable):
        executable = os.path.join(ROOT, executable.lstrip("./").replace("/", os.sep))
    if not os.path.exists(executable):
        executable = "git"
    return {
        "git": executable,
        "branch": git.get("Branch") or "master",
        "enabled": git.get("SyncUpstream", True),
        "timeout": int(git.get("SyncUpstreamTimeout", 120)),
    }


class Repo:
    def __init__(self, git, timeout=120):
        self.git = git
        self.timeout = timeout

    def run(self, *args, **kwargs):
        """Run a git command, return (returncode, stdout, stderr) as text."""
        check = kwargs.pop("check", False)
        timeout = kwargs.pop("timeout", self.timeout)
        cmd = [self.git, "-c", "core.quotepath=false"] + list(args)
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            timeout=timeout,
            # git writes paths as utf-8; never let a console codepage break them
            encoding="utf-8",
            errors="replace",
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if check and proc.returncode != 0:
            raise RuntimeError("git %s failed: %s" % (" ".join(args), err or out))
        return proc.returncode, out, err

    def out(self, *args, **kwargs):
        return self.run(*args, **kwargs)[1]

    def dirty_tracked(self):
        """Tracked files with uncommitted changes. Untracked ones are fine."""
        _, out, _ = self.run("status", "--porcelain", "--untracked-files=no")
        return [line for line in out.splitlines() if line.strip()]

    def head(self):
        return self.out("rev-parse", "HEAD")


def _is_theirs(path):
    if path in THEIRS:
        return True
    if path.startswith("tests/island/transport/"):
        return True
    for prefix in THEIRS_PREFIX:
        if path.startswith(prefix):
            return True
    return False


def _resolve_theirs(repo, path):
    code, _, err = repo.run("checkout", "--theirs", "--", path)
    if code != 0:
        # A path only upstream has (no conflict, just added) needs no resolution
        return False, err
    repo.run("add", "--", path)
    return True, ""


def _resolve_i18n(repo, path):
    """Keep the members of both sides of every conflict block.

    Upstream appends its new options at the same place in the file where this
    branch appends its own, so taking either side silently drops the other.
    Both blocks end on the last value of a member and share the closing brace,
    so the local block only needs its own member closed before the upstream one
    follows.
    """
    full = os.path.join(ROOT, path.replace("/", os.sep))
    with open(full, "r", encoding="utf-8") as f:
        text = f.read()
    if "<<<<<<<" not in text:
        repo.run("add", "--", path)
        return True, "no conflict marker"

    def both_sides(match):
        ours, theirs = match.group(1), match.group(2)
        if not theirs.strip():
            return ours
        if not ours.strip():
            return theirs
        indent = re.match(r"([ \t]*)\S", ours)
        indent = indent.group(1) if indent else ""
        return ours.rstrip() + "\n" + indent + "},\n" + theirs

    text = CONFLICT_RE.sub(both_sides, text)
    if "<<<<<<<" in text or ">>>>>>>" in text:
        return False, "conflict markers left after resolution"
    try:
        json.loads(text)
    except Exception as e:
        return False, "invalid json: %s" % e
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    repo.run("add", "--", path)
    return True, ""


def _asset_definitions(text):
    """{name: line} for every `NAME = Button(...)` / `Template(...)` line."""
    found = {}
    for line in text.splitlines():
        match = ASSET_DEF_RE.match(line)
        if match:
            found[match.group(1)] = line
    return found


def _resolve_ours(repo, path):
    """Keep the local side of every conflict block, follow upstream elsewhere."""
    full = os.path.join(ROOT, path.replace("/", os.sep))
    with open(full, "r", encoding="utf-8") as f:
        text = f.read()
    if "<<<<<<<" not in text:
        repo.run("add", "--", path)
        return True, "no conflict marker"

    text = CONFLICT_RE.sub(lambda m: m.group(1), text)
    if "<<<<<<<" in text or ">>>>>>>" in text:
        return False, "conflict markers left after resolution"
    if path.endswith(".py"):
        try:
            compile(text, path, "exec")
        except SyntaxError as e:
            return False, "syntax error after resolution: %s" % e
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    repo.run("add", "--", path)
    return True, "kept the local side of the conflict"


def _resolve_assets(repo, path, upstream_ref):
    """Upstream file plus whatever local definitions upstream does not have."""
    ours_text = repo.run("show", "HEAD:" + path)[1]
    theirs_text = repo.run("show", "%s/%s:%s" % (UPSTREAM, upstream_ref, path))[1]
    ours = _asset_definitions(ours_text)
    theirs = _asset_definitions(theirs_text)

    local_only = [name for name in ours if name not in theirs]
    merged = theirs_text.rstrip("\n") + "\n"
    if local_only:
        # Append after the last definition so the file stays sorted by name
        merged += "".join(ours[name] + "\n" for name in sorted(local_only))
    with open(os.path.join(ROOT, path.replace("/", os.sep)), "w", encoding="utf-8", newline="\n") as f:
        f.write(merged)
    repo.run("add", "--", path)
    return True, "%d upstream definitions, %s kept from local" % (
        len(theirs),
        ", ".join(local_only) if local_only else "nothing",
    )


def _collect_i18n_keys(node, prefix=""):
    """Every path in the tree, leaves and intermediate nodes alike.

    `IslandRestaurant.StrictWaitress` is a dict holding name/help, so collecting
    leaves only would miss the very keys this branch has to keep.
    """
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            path = prefix + key
            found.add(path)
            found |= _collect_i18n_keys(value, path + ".")
    return found


def check_i18n_keys(repo, before_commit):
    """Verify the branch's own i18n keys survived the merge."""
    missing = []
    for path in I18N_FILES:
        before = _collect_i18n_keys(json.loads(repo.run("show", "%s:%s" % (before_commit, path))[1]))
        with open(os.path.join(ROOT, path.replace("/", os.sep)), "r", encoding="utf-8") as f:
            after = _collect_i18n_keys(json.load(f))
        for key in KEEP_I18N_KEYS:
            if key in before and key not in after:
                missing.append("%s:%s" % (path, key))
    return missing


def smoke_test(repo, changed_files):
    """Compile what changed, then import the island modules."""
    py_files = [p for p in changed_files if p.endswith(".py") and os.path.exists(os.path.join(ROOT, p.replace("/", os.sep)))]
    if py_files:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile"] + py_files,
            cwd=ROOT,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            return "py_compile failed: %s" % ((proc.stderr or proc.stdout or "").strip()[:400])

    code = "import " + ", ".join(SMOKE_IMPORTS)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        return "import failed: %s" % " | ".join(tail)
    return ""


def sync(dry_run=False, push=False):
    """Merge upstream into this branch. Returns SyncResult.

    Never raises: a broken sync must not block startup.
    """
    config = read_deploy_config()
    if not config["enabled"]:
        return SyncResult("disabled", "Deploy.Git.SyncUpstream is false")

    repo = Repo(config["git"], config["timeout"])
    branch = config["branch"]

    try:
        if not os.path.isdir(os.path.join(ROOT, ".git")):
            return SyncResult("disabled", "not a git checkout")

        dirty = repo.dirty_tracked()
        if dirty:
            logger.warning("Upstream sync: %d tracked file(s) modified, skip" % len(dirty))
            return SyncResult("dirty", "%d tracked files modified" % len(dirty), dirty)

        code, _, err = repo.run("fetch", UPSTREAM, branch, timeout=config["timeout"])
        if code != 0:
            return SyncResult("offline", err or "git fetch failed")

        behind = repo.out("rev-list", "--count", "HEAD..%s/%s" % (UPSTREAM, branch))
        if not behind or behind == "0":
            return SyncResult("up-to-date", "")

        before = repo.head()
        target = "%s/%s" % (UPSTREAM, branch)
        message = (
            "Merge upstream %s into %s\n\n"
            "Applied by module/webui/upstream_sync.py at startup. Island transport\n"
            "follows upstream, island assets are merged per definition, i18n takes\n"
            "the upstream wording while keeping the branch's own keys.\n"
            % (repo.out("rev-parse", "--short", target), branch)
        )

        logger.hr("Merge upstream", 0)
        logger.info("%s new commit(s) in %s" % (behind, target))

        # Trial merge, then classify. Deliberately not `merge-tree --write-tree`:
        # the bundled git is 2.28 and needs 2.38 for that form, where it prints
        # usage and exits non-zero with no file list, so every conflict would
        # slip through the check unnoticed.
        code, out, err = repo.run("merge", "--no-ff", "--no-commit", target)
        if code != 0 and "CONFLICT" not in (out + err):
            repo.run("merge", "--abort")
            return SyncResult("failed", err or out)

        _, conflicted, _ = repo.run("diff", "--name-only", "--diff-filter=U")
        paths = [p for p in conflicted.splitlines() if p.strip()]

        unknown = [
            p
            for p in paths
            if not (_is_theirs(p) or p in I18N_FILES or p in OURS or p == ASSETS_FILE)
        ]
        if unknown:
            repo.run("merge", "--abort")
            logger.warning(
                "Upstream sync: %s new commit(s) but %d conflict(s) need a human,"
                " kept the current code" % (behind, len(unknown))
            )
            for path in unknown:
                logger.warning("  needs manual merge: %s" % path)
            return SyncResult("needs-manual", "%s commit(s) behind" % behind, unknown)

        if dry_run:
            repo.run("merge", "--abort")
            logger.info(
                "Upstream sync (dry run): %s commit(s) behind, %d conflict(s), all known: %s"
                % (behind, len(paths), ", ".join(paths) or "none")
            )
            return SyncResult("dry-run", "%s commit(s) behind" % behind, paths)

        notes = []
        for path in paths:
            if _is_theirs(path):
                ok, detail = _resolve_theirs(repo, path)
            elif path in I18N_FILES:
                ok, detail = _resolve_i18n(repo, path)
            elif path in OURS:
                ok, detail = _resolve_ours(repo, path)
            elif path == ASSETS_FILE:
                ok, detail = _resolve_assets(repo, path, branch)
            else:
                ok, detail = False, "unexpected conflict"
            if not ok:
                repo.run("reset", "--hard", before)
                return SyncResult("failed", "%s: %s" % (path, detail), [path])
            notes.append("%s (%s)" % (path, detail))

        # Drop local tests upstream has superseded, but only when upstream
        # really does not ship that path itself.
        for path in sorted(OBSOLETE_FILES):
            if not os.path.exists(os.path.join(ROOT, path.replace("/", os.sep))):
                continue
            if repo.out("ls-tree", "--name-only", target, "--", path):
                continue
            repo.run("rm", "-q", "--", path)
            notes.append("%s removed, upstream has its own tests" % path)

        code, out, err = repo.run(
            "-c", "user.name=Alas Island",
            "-c", "user.email=island@localhost",
            "commit", "-m", message,
        )
        if code != 0:
            repo.run("reset", "--hard", before)
            return SyncResult("failed", "commit failed: %s" % (err or out))

        missing = check_i18n_keys(repo, before)
        if missing:
            repo.run("reset", "--hard", before)
            return SyncResult("failed", "i18n keys lost: %s" % ", ".join(missing), missing)

        changed = repo.out("diff", "--name-only", before, "HEAD").splitlines()
        problem = smoke_test(repo, [p for p in changed if p.strip()])
        if problem:
            repo.run("reset", "--hard", before)
            return SyncResult("failed", "smoke test reverted the merge: %s" % problem)

        logger.info("Upstream sync done: %s -> %s" % (before[:8], repo.head()[:8]))
        for note in notes:
            logger.info("  %s" % note)
        return SyncResult("merged", "%s commit(s), %d conflict(s)" % (behind, len(paths)), changed)

    except subprocess.TimeoutExpired:
        try:
            repo.run("merge", "--abort")
        except Exception:
            pass
        return SyncResult("offline", "git timed out")
    except Exception as e:
        logger.exception(e)
        return SyncResult("failed", str(e))


def startup_sync():
    """Entry point called from gui.py before the webui imports its modules."""
    try:
        result = sync()
    except Exception as e:  # sync() should already be safe, belt and braces
        logger.exception(e)
        return None

    if result.status == "merged":
        logger.info("Alas source code was updated to upstream, running the new code")
    elif result.status == "needs-manual":
        logger.warning(
            "Upstream has %s but %d file(s) need a human merge, kept the current code"
            % (result.detail, len(result.files))
        )
    elif result.status in ("offline", "disabled", "dirty"):
        logger.info("Upstream sync skipped: %s" % (result.detail or result.status))
    return result


if __name__ == "__main__":
    logger.info(sync(dry_run="--dry-run" in sys.argv).__repr__())
