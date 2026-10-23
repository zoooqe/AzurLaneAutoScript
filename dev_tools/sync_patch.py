"""Sync island changes into another Alas checkout by patching, not replacing.

The target directory is a working install that may hold its own local tweaks,
so every text file is compared line by line and only the hunks that actually
differ are written. Everything else in the file stays byte-identical.

Line endings are normalised before diffing and restored afterwards: a file
that only differs in CRLF vs LF is reported as unchanged instead of being
rewritten, and a real patch keeps the target's original line endings.

Binary files cannot be patched and are copied as a whole, but only when they
differ. Every touched file is backed up first, so a run can be reverted.

Usage:
    python dev_tools/sync_patch.py --target F:/AzurLaneAutoScript            # dry run
    python dev_tools/sync_patch.py --target F:/AzurLaneAutoScript --apply    # write
    python dev_tools/sync_patch.py --target F:/AzurLaneAutoScript --restore 20261023_150000

Exit codes: 0 = clean, 1 = some files could not be patched, 2 = bad arguments.
"""
import argparse
import filecmp
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Only these trees are ever considered. config/ holds user data (menus, island
# settings) and must never be touched; log/ and .git/ are runtime leftovers.
INCLUDE_DIRS = ('module', 'assets/island', 'dev_tools', 'tests')
# Per-server island art lives beside the generic set (assets/cn/island,
# assets/jp/island, ...). A feature that adds a template also adds the
# per-server pieces, so glob them instead of listing each server by hand.
INCLUDE_DIR_GLOBS = ('assets/*/island',)
INCLUDE_ROOT_FILES = ('VERIFY.md', 'ADD_ITEM_TEMPLATE.md', 'alas.py', 'gui.py')
# new_templates/ is where the runtime dumps unreadable templates for triage.
SKIP_DIRS = {'__pycache__', '.git', 'tmp', 'preview', 'new_templates'}
SKIP_SUFFIXES = ('.pyc', '.pyo', '.orig', '.rej')
BINARY_SUFFIXES = ('.png', '.jpg', '.jpeg', '.bmp', '.ico', '.ttf', '.otf', '.onnx', '.bin', '.zip')
BACKUP_DIRNAME = '.sync_backup'


def include_dirs(root):
    """Directories to scan, expanded from the fixed list plus the globs."""
    dirs = list(INCLUDE_DIRS)
    for pattern in INCLUDE_DIR_GLOBS:
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            if not os.path.isdir(path):
                continue
            rel = os.path.relpath(path, root).replace(os.sep, '/')
            if rel not in dirs:
                dirs.append(rel)
    return dirs


def rel_files(root):
    """Relative paths of the files this tool cares about."""
    found = []
    for include in include_dirs(root):
        base = os.path.join(root, include)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if name.endswith(SKIP_SUFFIXES):
                    continue
                found.append(os.path.relpath(os.path.join(dirpath, name), root).replace(os.sep, '/'))
    for name in INCLUDE_ROOT_FILES:
        if os.path.isfile(os.path.join(root, name)):
            found.append(name)
    return sorted(found)


def is_binary(path):
    if path.lower().endswith(BINARY_SUFFIXES):
        return True
    with open(path, 'rb') as f:
        return b'\0' in f.read(8192)


def detect_eol(data):
    crlf = data.count(b'\r\n')
    lf = data.count(b'\n') - crlf
    return b'\r\n' if crlf > lf else b'\n'


def backup(target, rel, stamp):
    dst = os.path.join(target, BACKUP_DIRNAME, stamp, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(os.path.join(target, rel), dst)


def diff_and_patch(dst_data, src_data, rel, apply_changes):
    """Diff two byte strings line-wise and apply the result.

    Returns (state, detail). Nothing under `target` is written here: the diff
    runs in a scratch directory, so a failed patch leaves the file untouched.
    """
    dst_eol = detect_eol(dst_data)
    with tempfile.TemporaryDirectory() as tmp:
        dst_tmp = os.path.join(tmp, rel)
        src_tmp = os.path.join(tmp, 'new', rel)
        os.makedirs(os.path.dirname(dst_tmp), exist_ok=True)
        os.makedirs(os.path.dirname(src_tmp), exist_ok=True)
        with open(dst_tmp, 'wb') as f:
            f.write(dst_data.replace(b'\r\n', b'\n'))
        with open(src_tmp, 'wb') as f:
            f.write(src_data.replace(b'\r\n', b'\n'))

        patch_file = os.path.join(tmp, 'change.patch')
        diff = subprocess.run(
            ['diff', '-u', '--label', 'a/' + rel, '--label', 'b/' + rel, dst_tmp, src_tmp],
            stdout=subprocess.PIPE,
        )
        with open(patch_file, 'wb') as f:
            f.write(diff.stdout)
        hunks = diff.stdout.count(b'\n@@')
        if not hunks:
            return 'same', ''
        if not apply_changes:
            return 'patch', '{} hunk(s)'.format(hunks)

        applied = subprocess.run(
            ['patch', '-p1', '-d', tmp, '--forward', '--batch',
             '--no-backup-if-mismatch', '--reject-file=-', '-i', patch_file],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if applied.returncode != 0:
            lines = [l for l in applied.stdout.decode('utf-8', 'replace').splitlines() if l.strip()]
            return 'conflict', ' | '.join(lines[:3])

        with open(dst_tmp, 'rb') as f:
            result = f.read()
    if dst_eol == b'\r\n':
        result = result.replace(b'\n', b'\r\n')
    return 'patched', (result, hunks)


def sync_file(source, target, rel, stamp, apply_changes):
    src = os.path.join(source, rel)
    dst = os.path.join(target, rel)

    if not os.path.exists(dst):
        if apply_changes:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        return 'added', ''

    if filecmp.cmp(src, dst, shallow=False):
        return 'same', ''

    if is_binary(rel):
        if apply_changes:
            backup(target, rel, stamp)
            shutil.copy2(src, dst)
        return 'binary', ''

    with open(dst, 'rb') as f:
        dst_data = f.read()
    with open(src, 'rb') as f:
        src_data = f.read()

    if dst_data.replace(b'\r\n', b'\n') == src_data.replace(b'\r\n', b'\n'):
        return 'same', ''

    state, detail = diff_and_patch(dst_data, src_data, rel, apply_changes)
    if state == 'patched':
        result, hunks = detail
        backup(target, rel, stamp)
        with open(dst, 'wb') as f:
            f.write(result)
        return 'patched', '{} hunk(s)'.format(hunks)
    return state, detail


def restore(target, stamp):
    root = os.path.join(target, BACKUP_DIRNAME, stamp)
    if not os.path.isdir(root):
        print('No backup named {} under {}'.format(stamp, target))
        return 2
    count = 0
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            src = os.path.join(dirpath, name)
            dst = os.path.join(target, os.path.relpath(src, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
            count += 1
    print('Restored {} file(s) from backup {}'.format(count, stamp))
    print('Files added by that run are not removed; delete them by hand if needed.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Patch a target checkout instead of overwriting files.')
    parser.add_argument('--source', default=SOURCE_ROOT, help='checkout holding the new code')
    parser.add_argument('--target', required=True, help='checkout to update (user install)')
    parser.add_argument('--apply', action='store_true', help='write the changes (default: dry run)')
    parser.add_argument('--restore', metavar='STAMP', help='revert a previous run by its backup stamp')
    args = parser.parse_args()

    source = os.path.abspath(args.source)
    target = os.path.abspath(args.target)
    if not os.path.isdir(target):
        print('Target not found: {}'.format(target))
        return 2
    if os.path.normcase(source) == os.path.normcase(target):
        print('Source and target are the same directory')
        return 2
    if args.restore:
        return restore(target, args.restore)

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    counts = {}
    details = []
    for rel in rel_files(source):
        if not os.path.exists(os.path.join(source, rel)):
            continue
        state, extra = sync_file(source, target, rel, stamp, args.apply)
        counts[state] = counts.get(state, 0) + 1
        if state != 'same':
            details.append((state, rel, extra))

    print('{}  {} -> {}'.format('APPLY' if args.apply else 'DRY RUN', source, target))
    print('-' * 78)
    for state, rel, extra in details:
        line = '{:>9}  {}'.format(state, rel)
        if extra:
            line += '   # ' + extra
        print(line)
    print('-' * 78)
    print(', '.join('{}: {}'.format(k, v) for k, v in sorted(counts.items())))
    if args.apply and (counts.get('added') or counts.get('patched')):
        print('Backup: {}'.format(os.path.join(target, BACKUP_DIRNAME, stamp)))
        print('Restart Alas so the new code is loaded.')
    conflicts = counts.get('conflict', 0)
    if conflicts:
        print('{} file(s) could not be patched and were left untouched.'.format(conflicts))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
