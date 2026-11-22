"""Rebuild IslandProduction.DailyBufferItems from the restaurant menus.

The island production planner picks the menus itself, so running it overwrites
whatever menu the user configured. This tool never touches the menus: it reads
them, works out how much of each item a day of selling that menu consumes, and
writes only DailyBufferItems.

The buffer is the stock band the task tries to keep (RecipeInfo.daily_buffer),
so a width of a day's consumption is what makes the production pick a recipe
about once a day.

The menu number is the planner's planned daily sale rate, not a shelf slot
count, and the planner may write a fraction of a tranche (佛跳墙 0.656). The
dish itself is covered by the restaurant reserve, which already holds a whole
shelf tranche, but its ingredients are not: sized by the fraction they end up
at 1, and the dish can never be produced a second time. So every listed dish
also contributes one full tranche (restaurant capacity) of its ingredients.

The development season is added on top: its tasks demand fixed amounts that the
menus never ask for (cloth, ore, tools), so those entries are kept even when no
restaurant sells anything that needs them. Amounts are the season demand itself,
expanded down to the raw materials, without any safety margin.

A delivered task is not a demand any more: `--done` records it in
config/island/season_progress.json and the rebuild subtracts its amount, so the
stock band shrinks to what the remaining tasks and the menus still need.

Usage:
    python dev_tools/island_buffer_from_menu.py            # dry run, prints the diff
    python dev_tools/island_buffer_from_menu.py --apply    # writes config/alas.json
    python dev_tools/island_buffer_from_menu.py --done 80001502,80001504 --apply
    python dev_tools/island_buffer_from_menu.py --undo 80001502 --apply
"""
import io
import json
import math
import sys
from collections import Counter

sys.path.insert(0, '.')

import module.config.server as server
from module.island.data import DIC_ISLAND_ITEM
from module.island.season_progress import (
    clear_completed,
    get_active_season,
    get_completed_entries,
    get_completed_task_ids,
    get_task_name,
    mark_completed,
)
from module.island.utils import (
    ceil_with_epsilon,
    get_stuck_season_order_items,
    item_mapping_to_yaml,
    load_item_mapping,
    normalize_item_keys,
)
from module.island_handler.restaurant_config import (
    RESTAURANT_CONFIG,
    get_restaurant_capacity,
)
from module.island_handler.season_priority import (
    PRODUCERS,
    RECIPE_PRODUCTS,
    expand_items,
    get_active_season_id,
    get_season_task_items,
)

CONFIG = 'config/alas.json'
APPLY = '--apply' in sys.argv
BUFFER_PATH = ('IslandProduction', 'IslandProduction', 'DailyBufferItems')
MANUAL_PATH = ('IslandProduction', 'IslandProduction', 'ManualBufferItems')
ORDER_PATH = ('IslandOrder', 'IslandOrder', 'StuckSeasonOrderId')

MENU_KEYS = tuple(
    (restaurant_id, data['menu_key'])
    for restaurant_id, data in RESTAURANT_CONFIG.items()
)


class RawConfig:
    """Minimal `cross_get` so restaurant_config can read the raw config json."""

    def __init__(self, raw):
        self.raw = raw

    def cross_get(self, key, default=None):
        node = self.raw
        for part in key.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node


def option_ids(name):
    """Task ids given as `--name 80001502,80001504`."""
    if name not in sys.argv:
        return set()
    index = sys.argv.index(name)
    if index + 1 >= len(sys.argv):
        raise SystemExit(f'{name} needs a comma separated task id list')
    return {int(part) for part in sys.argv[index + 1].split(',') if part.strip()}



def name(item_id):
    return DIC_ISLAND_ITEM.get(item_id, {}).get('name', {}).get(server.server, str(item_id))


def is_buffer_item(item_id):
    # Seeds, fry and feed are bought per planting, they are not production stock.
    if item_id < 2000:
        return False
    # Ore and wood have no recipe at all, mining and logging only list coal and
    # timber as samples, so anything the chain consumes without a producer is a
    # raw material and needs a band as well.
    return item_id in RECIPE_PRODUCTS or item_id not in PRODUCERS


def diff_paths(a, b, prefix=''):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for key in set(a) | set(b):
            out += diff_paths(a.get(key), b.get(key), prefix + '.' + str(key))
        return out
    return [] if a == b else [prefix]


def main():
    raw = json.load(io.open(CONFIG, encoding='utf-8'))
    restaurant = raw['IslandBusiness']['IslandRestaurant']

    config = RawConfig(raw)

    # A delivered season task is no longer a demand. The record is written only
    # with --apply, a dry run simulates the change instead.
    done = option_ids('--done')
    undo = option_ids('--undo')
    if APPLY:
        if undo:
            removed = clear_completed(undo, config)
            print(f'forgot {len(removed)} delivered tasks: {sorted(removed)}')
        if done:
            added = mark_completed(done, config)
            print(f'recorded {len(added)} delivered tasks: {sorted(added)}')
    completed = (get_completed_task_ids(config) | done) - undo

    menu_demand = Counter()
    menu_tranche = Counter()
    for restaurant_id, key in MENU_KEYS:
        menu = normalize_item_keys(load_item_mapping(restaurant[key], config_name=key))
        capacity = get_restaurant_capacity(config, restaurant_id)
        print(f'{key}: {len(menu)} dishes, shelf capacity {capacity}')
        menu_demand.update(menu)
        for dish_id, amount in menu.items():
            if ceil_with_epsilon(amount) <= 0:
                continue
            # The restaurant only lists a dish once a whole shelf tranche is in
            # stock, and then sells the tranche at once. The dish itself is
            # covered by the menu reserve, but its ingredients are not, so one
            # tranche of the recipe chain has to stay producible.
            tranche = expand_items({dish_id: capacity})
            tranche.pop(dish_id, None)
            for item_id, quantity in tranche.items():
                if quantity > menu_tranche.get(item_id, 0):
                    menu_tranche[item_id] = quantity

    expanded = expand_items(menu_demand)
    # Seeds, feed and fish are gathered or bought, they are not production stock.
    menu_buffer = {}
    for item_id in set(expanded) | set(menu_tranche):
        if item_id not in RECIPE_PRODUCTS:
            continue
        amount = max(expanded.get(item_id, 0), menu_tranche.get(item_id, 0))
        if amount > 0:
            menu_buffer[item_id] = math.ceil(amount - 1e-9)
    # Ingredients the tranche rule lifted above the planned daily sale rate.
    tranche_sized = {
        item_id: menu_tranche[item_id]
        for item_id in menu_tranche
        if menu_tranche[item_id] > expanded.get(item_id, 0) and item_id in menu_buffer
    }

    # Development season: the tasks ask for fixed amounts, keep them whether or
    # not a menu consumes them.
    season_id = get_active_season_id(config)
    targets = get_season_task_items(config, completed_ids=completed)
    season_expanded = expand_items(targets) if targets else Counter()
    season_buffer = {
        item_id: math.ceil(amount - 1e-9)
        for item_id, amount in season_expanded.items()
        if amount > 0 and (item_id in targets or is_buffer_item(item_id))
    }
    buffer = dict(menu_buffer)
    for item_id, amount in season_buffer.items():
        if amount > buffer.get(item_id, 0):
            buffer[item_id] = amount

    old = normalize_item_keys(load_item_mapping(
        raw[BUFFER_PATH[0]][BUFFER_PATH[1]][BUFFER_PATH[2]]
    ))

    # Entries the stuck season order still has to deliver keep their band,
    # everything else the current menu does not consume is stale.
    order_id = raw[ORDER_PATH[0]][ORDER_PATH[1]].get(ORDER_PATH[2], 0)
    order_items = set()
    if order_id:
        order_items = set(get_stuck_season_order_items(
            order_id, get_active_season(config)[1])[1])

    stale = {i: v for i, v in old.items() if i not in buffer}
    kept = {i: v for i, v in stale.items() if i in order_items}
    dropped = {i: v for i, v in stale.items() if i not in order_items}
    merged = dict(buffer)
    merged.update(kept)

    # A hand raised value is never thrown away: it moves to ManualBufferItems,
    # which recipe.py merges by max on top of the buffer. Dishes are excluded,
    # the restaurant reserve already keeps a whole shelf tranche of them, so a
    # buffer above their sell rate would stack a second tranche on top.
    previous_manual = normalize_item_keys(load_item_mapping(
        raw[MANUAL_PATH[0]][MANUAL_PATH[1]][MANUAL_PATH[2]]
    ))
    manual = dict(previous_manual)
    raised = {
        item_id: value for item_id, value in old.items()
        if value > merged.get(item_id, 0) and item_id not in menu_demand
    }
    for item_id, value in raised.items():
        if value > manual.get(item_id, 0):
            manual[item_id] = value

    print(f'\nmenu dishes={len(menu_demand)} menu buffer={len(menu_buffer)} '
          f'tranche sized={len(tranche_sized)} '
          f'season {season_id or "-"} tasks={len(targets)} items '
          f'season buffer={len(season_buffer)} '
          f'kept(season order)={len(kept)} dropped={len(dropped)} total={len(merged)}')

    print(f'\n[season progress] {len(completed)} tasks delivered')
    for task_id in sorted(completed):
        print(f'  {get_task_name(task_id)} ({task_id}): no longer produced')

    print('\n[menu dishes]')
    for item_id, amount in sorted(menu_demand.items()):
        print(f'  {name(item_id)} ({item_id}): sell {amount:g}/day -> buffer {buffer.get(item_id, 0)}')

    print(f'\n[menu ingredients sized to one shelf tranche] {len(tranche_sized)} items')
    for item_id in sorted(tranche_sized):
        print(f'  {name(item_id)} ({item_id}): day {expanded.get(item_id, 0):g} -> '
              f'tranche {tranche_sized[item_id]:g} -> buffer {buffer.get(item_id, 0)}')

    print('\n[season tasks]')
    for item_id, amount in sorted(targets.items()):
        print(f'  {name(item_id)} ({item_id}): need {amount:g} -> buffer {season_buffer.get(item_id, 0)}')

    print('\n[season materials]')
    for item_id in sorted(season_buffer):
        if item_id not in targets:
            print(f'  {name(item_id)} ({item_id}): {season_buffer[item_id]}')

    print('\n[changed]')
    for item_id in sorted(merged):
        before = old.get(item_id)
        after = merged[item_id]
        if before != after:
            print(f'  {name(item_id)} ({item_id}): '
                  f'{"new" if before is None else f"{before:g}"} -> {after}')

    print('\n[kept: stuck season order]')
    for item_id, amount in sorted(kept.items()):
        print(f'  {name(item_id)} ({item_id}): {amount}')

    print('\n[dropped: not in the current menu]')
    for item_id, amount in sorted(dropped.items()):
        print(f'  {name(item_id)} ({item_id}): {amount}')

    print('\n[manual: kept from the old buffer]')
    for item_id, value in sorted(manual.items()):
        tag = 'new' if item_id not in previous_manual else f'{previous_manual[item_id]:g}'
        print(f'  {name(item_id)} ({item_id}): {tag} -> {value}')

    if not APPLY:
        print('\n(dry run, pass --apply to write)')
        return

    yaml_text = item_mapping_to_yaml(merged, use_item_name=True)
    manual_text = item_mapping_to_yaml(manual, use_item_name=True)
    before_all = json.loads(json.dumps(raw, ensure_ascii=False))
    raw[BUFFER_PATH[0]][BUFFER_PATH[1]][BUFFER_PATH[2]] = yaml_text
    allowed = ['.' + '.'.join(BUFFER_PATH)]
    if manual != previous_manual:
        raw[MANUAL_PATH[0]][MANUAL_PATH[1]][MANUAL_PATH[2]] = manual_text
        allowed.append('.' + '.'.join(MANUAL_PATH))
    changed = diff_paths(before_all, json.loads(json.dumps(raw, ensure_ascii=False)))
    if changed and set(changed) != set(allowed):
        raise SystemExit(f'Refusing to write, unexpected changes: {changed}')
    io.open(CONFIG, 'w', encoding='utf-8', newline='').write(
        json.dumps(raw, ensure_ascii=False, indent=2)
    )
    print(f'\nwritten {len(yaml_text.strip().splitlines())} buffer entries, '
          f'{len(manual)} manual entries, menus untouched')


if __name__ == '__main__':
    main()
