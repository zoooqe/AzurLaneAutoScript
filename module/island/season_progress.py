"""Which development season tasks have already been handed in.

The season tasks demand fixed amounts (500 citrus, 100 avocado, 25 silver ore,
25 elegant wood ...), and the daily buffer keeps that much stock so production
chases them. Once a task is delivered the demand is gone, so the buffer rebuild
and the priority ladder have to skip it, otherwise the island keeps producing
for a task that is already done.

The record lives next to the config, not in the repository: it describes one
instance of the game. It is keyed by season, so the next season starts with a
clean slate without anyone deleting the file.

Usage:
    from module.island.season_progress import (
        get_completed_task_ids, mark_completed, clear_completed)
    mark_completed({80001502, 80001504})
    get_completed_task_ids()   # {80001502, 80001504}
"""
import io
import json
import os
from datetime import datetime

import module.config.server as server
from module.island.data import DIC_ISLAND_TASK
from module.island.season_data import DIC_ISLAND_SEASON
from module.island.utils import get_season_data
from module.logger import logger

# The season in use is the namespace selected in IslandGeneral, see
# utils.get_configured_season. Namespaces look like `season_20261105_cn`.
SEASON_CONFIG_KEY = 'IslandGeneral.IslandGeneral.Season'

# Not in the config root: `alas_instance()` in module/config/utils.py lists every
# `config/*.json` as an Alas instance, so a state file there shows up as a
# phantom button in the GUI sidebar. A sub directory is not scanned.
PROGRESS_FILE = 'config/island/season_progress.json'
LEGACY_PROGRESS_FILE = 'config/island_season_progress.json'


def _migrate_legacy():
    """Move the state file out of the config root, it used to be an instance."""
    if not os.path.exists(LEGACY_PROGRESS_FILE):
        return
    if os.path.exists(PROGRESS_FILE):
        os.remove(LEGACY_PROGRESS_FILE)
        return
    folder = os.path.dirname(PROGRESS_FILE)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)
    os.replace(LEGACY_PROGRESS_FILE, PROGRESS_FILE)
    logger.info(f'Moved {LEGACY_PROGRESS_FILE} to {PROGRESS_FILE}')


def _default_season_name():
    """Newest season namespace of this server, for configs without the option."""
    names = [name for name in DIC_ISLAND_SEASON if name.endswith('_' + server.server)]
    return max(names) if names else None


def get_active_season(config=None, season_name=None):
    """(season name, season data) of the season in use, (0, None) when none is.

    The season is a namespace picked in IslandGeneral, not a date range, so the
    name is the identity used by the progress record.
    """
    name = season_name
    if not name and config is not None:
        name = config.cross_get(SEASON_CONFIG_KEY, None)
    if not name:
        # Older configs do not carry the option yet, use the newest season.
        name = _default_season_name()
    if not name:
        return 0, None
    try:
        return name, get_season_data(name)
    except KeyError:
        logger.warning(f'Unknown development season namespace {name}')
        return 0, None


def load_progress(path=None):
    """Raw progress record, {} when the file is missing or unreadable."""
    if path is None:
        _migrate_legacy()
    path = path or PROGRESS_FILE
    try:
        with io.open(path, encoding='utf-8') as file:
            data = json.load(file)
    except (IOError, OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_progress(data, path=None):
    if path is None:
        _migrate_legacy()
    path = path or PROGRESS_FILE
    folder = os.path.dirname(path)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)
    with io.open(path, 'w', encoding='utf-8', newline='\n') as file:
        file.write(json.dumps(data, ensure_ascii=False, indent=2))
        file.write('\n')


def get_completed_entries(config=None, path=None, season_name=None):
    """Records of the delivered tasks of the configured season, [] otherwise."""
    season_id, season = get_active_season(config, season_name)
    if not season_id:
        return []
    data = load_progress(path)
    # Records written before the season namespaces were split stored the plain
    # season number, accept it as well so an upgrade does not lose the record.
    known = {season_id, (season or {}).get('id')}
    if data.get('season') not in known:
        # A record of an older season says nothing about this one.
        return []
    entries = data.get('completed')
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict) and 'id' in entry]


def get_completed_task_ids(config=None, path=None, season_name=None):
    """Task ids already delivered this season."""
    ids = set()
    for entry in get_completed_entries(config, path, season_name):
        try:
            ids.add(int(entry['id']))
        except (TypeError, ValueError):
            continue
    return ids


def get_task_name(task_id):
    name = DIC_ISLAND_TASK.get(task_id, {}).get('name') or {}
    return name.get(server.server) or str(task_id)


def mark_completed(task_ids, config=None, path=None, date=None, season_name=None):
    """Record the tasks as delivered, returns the ids that were new."""
    season_id, season = get_active_season(config, season_name)
    if not season_id:
        logger.warning(f'No development season is selected, cannot record {sorted(task_ids)}')
        return set()
    entries = get_completed_entries(config, path, season_name)
    known = set()
    for entry in entries:
        try:
            known.add(int(entry['id']))
        except (TypeError, ValueError):
            continue
    added = set()
    for task_id in sorted(task_ids):
        task_id = int(task_id)
        if task_id in known:
            continue
        added.add(task_id)
        entries.append({
            'id': task_id,
            'name': get_task_name(task_id),
            'date': date or _now_text(),
        })
    if added:
        save_progress({'season': season_id, 'completed': entries}, path)
    return added


def clear_completed(task_ids=None, config=None, path=None, season_name=None):
    """Forget the delivered tasks, all of them when task_ids is None."""
    season_id, _ = get_active_season(config, season_name)
    if not season_id:
        return set()
    entries = get_completed_entries(config, path, season_name)
    if task_ids is None:
        removed = {int(entry['id']) for entry in entries if str(entry.get('id', '')).isdigit()}
        entries = []
    else:
        drop = {int(task_id) for task_id in task_ids}
        removed = set()
        kept = []
        for entry in entries:
            try:
                entry_id = int(entry['id'])
            except (TypeError, ValueError):
                continue
            if entry_id in drop:
                removed.add(entry_id)
            else:
                kept.append(entry)
        entries = kept
    if removed:
        save_progress({'season': season_id, 'completed': entries}, path)
    return removed


def _now_text(time=None):
    time = time or datetime.now()
    return time.strftime('%Y-%m-%d %H:%M:%S')
