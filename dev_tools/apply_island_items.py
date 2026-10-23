"""Re-apply the island item picker feature.

Alas updates itself with `git reset --hard origin/master` + `git pull`, which
silently discards local changes in tracked files. `config/deploy.yaml` has
`AutoUpdate: false` now, but if the feature ever goes missing (manual update,
reinstalled git repo), run this script:

    ./toolkit/python.exe dev_tools/apply_island_items.py

It applies `dev_tools/island_items_feature.patch` and regenerates the config
files afterwards.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(ROOT, 'dev_tools', 'island_items_feature.patch')
PYTHON = os.path.join(ROOT, 'toolkit', 'python.exe')

# If every marker is present the feature is already installed.
MARKERS = [
    ('module/webui/widgets.py', 'island_items'),
    ('module/config/argument/argument.yaml', 'type: island_items'),
    ('module/island/item_editor.py', 'def items_from_text'),
]


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def git_executable():
    bundled = os.path.join(ROOT, 'toolkit', 'Git', 'mingw64', 'bin', 'git.exe')
    return bundled if os.path.exists(bundled) else 'git'


def missing():
    return [path for path, marker in MARKERS if marker not in read(path)]


def auto_update_warning():
    path = os.path.join(ROOT, 'config', 'deploy.yaml')
    if not os.path.exists(path):
        return
    with open(path, encoding='utf-8') as f:
        for line in f:
            if line.strip().startswith('AutoUpdate:'):
                if line.split(':', 1)[1].strip().lower() == 'true':
                    print('WARNING: AutoUpdate is true in config/deploy.yaml, '
                          'the next startup will discard this patch again.')
                return


def run(cmd, **kwargs):
    print('$', ' '.join(cmd))
    return subprocess.call(cmd, cwd=ROOT, **kwargs)


def main():
    if not os.path.exists(PATCH):
        print('Patch not found:', PATCH)
        return 1

    absent = missing()
    if not absent:
        print('Feature already installed, nothing to do.')
        auto_update_warning()
        return 0

    print('Missing in:', ', '.join(absent))
    git = git_executable()
    if run([git, 'apply', '--check', PATCH]) != 0:
        print('Patch does not apply cleanly, upstream code has changed.')
        print('Fix the conflicts manually, then regenerate config with:')
        print('  ./toolkit/python.exe -m module.config.config_updater')
        return 1

    if run([git, 'apply', PATCH]) != 0:
        print('Failed to apply patch.')
        return 1

    # args.json / config_generated.py / i18n are generated from the yaml files.
    if os.path.exists(PYTHON):
        run([PYTHON, '-m', 'module.config.config_updater'])

    left = missing()
    if left:
        print('Still missing in:', ', '.join(left))
        return 1

    print('Feature restored. Restart Alas to load it.')
    auto_update_warning()
    return 0


if __name__ == '__main__':
    sys.exit(main())
