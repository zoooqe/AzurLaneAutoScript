# -*- coding: utf-8 -*-
"""Record delivered development season tasks and drop their production chains.

A delivered task is no longer a demand: its items must leave DailyBufferItems
(the planner would keep producing for them) and ManualBufferItems (the hand
maintained copy added by island_manual_buffer.py). Only the item ids given on
the command line are touched, nothing is recalculated.

Usage:
    python dev_tools/island_deliver.py --done 80001505 --items 2521,5007
    python dev_tools/island_deliver.py --done 80001505 --items 2521,5007 --apply
"""
import argparse
import io
import json
import os
import re
import sys
from collections import OrderedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CONFIG = 'config/alas.json'
GROUP = ('IslandProduction', 'IslandProduction')
BUFFER_KEY = 'DailyBufferItems'
MANUAL_KEY = 'ManualBufferItems'

LINE_RE = re.compile(r'^(?P<name>.+?)\s*\((?P<id>\d+)\)\s*:\s*(?P<value>\d+)\s*$')


def parse_items(text):
    items = OrderedDict()
    for line in (text or '').splitlines():
        line = line.strip()
        if not line:
            continue
        match = LINE_RE.match(line)
        if not match:
            raise ValueError('Unrecognised item line: {!r}'.format(line))
        items[int(match.group('id'))] = (match.group('name'), int(match.group('value')))
    return items


def dump_items(items):
    if not items:
        return ''
    return '\n'.join('{} ({}): {}'.format(name, item_id, value)
                     for item_id, (name, value) in sorted(items.items())) + '\n'


def id_list(text):
    return [int(part) for part in text.split(',') if part.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--done', default='', help='comma separated task ids to record as delivered')
    parser.add_argument('--items', required=True, help='comma separated item ids to drop')
    parser.add_argument('--apply', action='store_true', help='write config/alas.json and the progress file')
    args = parser.parse_args()

    drop = id_list(args.items)
    done = id_list(args.done)

    with io.open(CONFIG, encoding='utf-8') as file:
        raw = json.load(file, object_pairs_hook=OrderedDict)
    group = raw[GROUP[0]][GROUP[1]]

    tables = OrderedDict()
    for key in (BUFFER_KEY, MANUAL_KEY):
        tables[key] = parse_items(group.get(key, ''))

    plan = OrderedDict()
    for key, items in tables.items():
        plan[key] = [(item_id,) + items[item_id] for item_id in drop if item_id in items]

    print('{:<20} {:>4} -> {:>4} entries'.format(
        BUFFER_KEY, len(tables[BUFFER_KEY]), len(tables[BUFFER_KEY]) - len(plan[BUFFER_KEY])))
    print('{:<20} {:>4} -> {:>4} entries'.format(
        MANUAL_KEY, len(tables[MANUAL_KEY]), len(tables[MANUAL_KEY]) - len(plan[MANUAL_KEY])))
    missing = [item_id for item_id in drop
               if item_id not in tables[BUFFER_KEY] and item_id not in tables[MANUAL_KEY]]
    for item_id in drop:
        hits = [key for key in tables if item_id in tables[key]]
        if hits:
            name, value = tables[hits[0]][item_id]
            print('  - {} ({}): {}  [{}]'.format(name, item_id, value, ', '.join(hits)))
        else:
            print('  ! {} not present in any table'.format(item_id))
    if missing and len(missing) == len(drop):
        print('Nothing to drop')
        return 1

    if not args.apply:
        print('\nDry run. Re-run with --apply to write.')
        return 0

    if done:
        from module.island.season_progress import mark_completed
        added = mark_completed(done)
        print('\nrecorded delivered tasks: {}'.format(sorted(added) or 'already recorded'))

    before = json.loads(json.dumps(raw, ensure_ascii=False))
    for key, items in tables.items():
        for item_id in drop:
            items.pop(item_id, None)
        group[key] = dump_items(items)
    after = json.loads(json.dumps(raw, ensure_ascii=False))

    changed = []
    for top in after:
        for sub in after[top]:
            for key in after[top][sub]:
                if before[top][sub].get(key) != after[top][sub].get(key):
                    changed.append('{}.{}.{}'.format(top, sub, key))
    expected = sorted('{}.{}.{}'.format(GROUP[0], GROUP[1], key)
                      for key in (BUFFER_KEY, MANUAL_KEY))
    if sorted(changed) != expected:
        print('Unexpected config changes: {}'.format(changed))
        return 2

    with io.open(CONFIG, 'w', encoding='utf-8', newline='\n') as file:
        file.write(json.dumps(raw, ensure_ascii=False, indent=2))
        file.write('\n')
    print('\nwritten {}.'.format(CONFIG))
    return 0


if __name__ == '__main__':
    sys.exit(main())
