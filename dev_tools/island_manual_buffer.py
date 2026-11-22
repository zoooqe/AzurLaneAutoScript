# -*- coding: utf-8 -*-
"""Copy hand-maintained production chains from DailyBufferItems into ManualBufferItems.

The production planner rewrites DailyBufferItems wholesale (see
module/island_handler/production_planner.py:161), so anything the planner does not
know about is dropped. ManualBufferItems is never written by the planner and is
merged per item by max (module/island_handler/recipe.py:491-496), which makes it
the safe place to keep hand-maintained chains.

This script only reads DailyBufferItems and merges the selected item ids into
ManualBufferItems, keeping the larger value. It is idempotent.

Usage:
    python dev_tools/island_manual_buffer.py            # dry run
    python dev_tools/island_manual_buffer.py --apply    # write config/alas.json
"""
import argparse
import io
import json
import re
import sys
from collections import OrderedDict

CONFIG = 'config/alas.json'
GROUP = ('IslandProduction', 'IslandProduction')
BUFFER_KEY = 'DailyBufferItems'
MANUAL_KEY = 'ManualBufferItems'

# Season-task chains that are not covered by IslandSeasonTask.TaskTarget, so the
# planner cannot derive them: 淡水鱼肉, 香囊, 蓄电池, 草莓夏洛特, 禽肉土豆拼盘.
CHAIN_IDS = [
    # 淡水鱼肉
    2521, 5007,
    # 香囊
    3039, 3035, 2010,
    # 蓄电池
    3055, 3045, 2704,
    # 草莓夏洛特
    3028, 3006, 2603, 2008,
    # 禽肉土豆拼盘
    3030, 2005, 2602,
]

LINE_RE = re.compile(r'^(?P<name>.+?)\s*\((?P<id>\d+)\)\s*:\s*(?P<value>\d+)\s*$')


def parse_items(text):
    """Parse `name (id): value` YAML text into an ordered {id: (name, value)} dict."""
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
    return '\n'.join('{} ({}): {}'.format(name, item_id, value)
                     for item_id, (name, value) in sorted(items.items()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', help='write config/alas.json')
    args = parser.parse_args()

    with io.open(CONFIG, encoding='utf-8') as f:
        raw = json.load(f, object_pairs_hook=OrderedDict)

    group = raw[GROUP[0]][GROUP[1]]
    buffer_items = parse_items(group.get(BUFFER_KEY, ''))
    manual_items = parse_items(group.get(MANUAL_KEY, ''))

    missing = [item_id for item_id in CHAIN_IDS if item_id not in buffer_items]
    if missing:
        print('Not found in {}: {}'.format(BUFFER_KEY, missing))
        return 1

    merged = OrderedDict(manual_items)
    added, raised, same = [], [], []
    for item_id in CHAIN_IDS:
        name, value = buffer_items[item_id]
        if item_id not in merged:
            merged[item_id] = (name, value)
            added.append((item_id, name, value))
        elif merged[item_id][1] < value:
            old = merged[item_id][1]
            merged[item_id] = (name, value)
            raised.append((item_id, name, old, value))
        else:
            same.append((item_id, name, merged[item_id][1]))

    print('added  {}:'.format(len(added)))
    for item_id, name, value in added:
        print('  + {} ({}) = {}'.format(name, item_id, value))
    if raised:
        print('raised {}:'.format(len(raised)))
        for item_id, name, old, value in raised:
            print('  ^ {} ({}) {} -> {}'.format(name, item_id, old, value))
    if same:
        print('kept   {} already covered: {}'.format(
            len(same), ', '.join(name for _, name, _ in same)))

    text = dump_items(merged)
    print('\n{}: {} -> {} entries'.format(MANUAL_KEY, len(manual_items), len(merged)))

    if not args.apply:
        print('\nDry run. Re-run with --apply to write {}.'.format(CONFIG))
        print('\n' + text)
        return 0

    before = json.loads(json.dumps(raw, ensure_ascii=False))
    group[MANUAL_KEY] = text
    after = json.loads(json.dumps(raw, ensure_ascii=False))

    changed = []
    for top in after:
        for sub in after[top]:
            if before[top].get(sub) != after[top].get(sub):
                for key in after[top][sub]:
                    if before[top][sub].get(key) != after[top][sub].get(key):
                        changed.append('{}.{}.{}'.format(top, sub, key))
    expected = ['{}.{}.{}'.format(GROUP[0], GROUP[1], MANUAL_KEY)]
    if sorted(changed) != sorted(expected):
        print('Unexpected config changes: {}'.format(changed))
        return 2

    with io.open(CONFIG, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(raw, ensure_ascii=False, indent=2))
        f.write('\n')
    print('\nwritten {}.'.format(CONFIG))
    print('Note: {} was left untouched, so both tables now carry these items '
          'and the larger value wins.'.format(BUFFER_KEY))
    return 0


if __name__ == '__main__':
    sys.exit(main())
