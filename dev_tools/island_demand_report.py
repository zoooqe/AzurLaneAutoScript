"""Print what every island sub task asks production for.

Reads the live config, so the numbers match what the production sequence uses:
the restaurant menus, the development season, and the idle accumulation rates.
Orders and transport commissions have no static demand -- their items are
scanned from the screen and change every refresh -- so the report lists the
stock they are allowed to take instead: the free band above the hard floor and
the menu reserve.

Usage:
    ./toolkit/python.exe dev_tools/island_demand_report.py
    ./toolkit/python.exe dev_tools/island_demand_report.py --tier 2   # one tier only
"""

import sys

sys.path.insert(0, '.')

import module.config.server as server
from module.config.config import AzurLaneConfig
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_TASK
from module.island.season_progress import get_active_season
from module.island.utils import load_hard_floor_items, load_item_mapping, normalize_item_keys
from module.island_handler.restaurant_config import (
    RESTAURANT_IDS,
    get_config_key,
    get_menu_reserve_items,
    get_restaurant_capacity,
    get_restaurant_config,
)
from module.island_handler.season_priority import (
    TIER_IDLE,
    TIER_MENU,
    TIER_NAMES,
    TIER_SEASON,
    RECIPE_PRODUCTS,
    TIER_SURPLUS,
    build_demand_ledger,
    expand_items,
    get_active_season_id,
    get_free_band,
    get_priority_tier,
    get_season_delivery_items,
    get_season_task_items,
)

CONFIG = 'config/alas.json'
BUFFER_KEY = 'IslandProduction.IslandProduction.DailyBufferItems'


def name(item_id):
    return DIC_ISLAND_ITEM.get(item_id, {}).get('name', {}).get(server.server, str(item_id))


def load_buffer(config):
    return normalize_item_keys(load_item_mapping(
        config.cross_get(BUFFER_KEY, '') or '', config_name='DailyBufferItems',
    ))


def print_restaurants(config):
    print('=' * 78)
    print('[1] 店铺经营 — 每个营业周期卖出的菜品（缓冲 = 一个售卖批次）')
    print('=' * 78)
    dishes = {}
    for restaurant_id in RESTAURANT_IDS:
        data = get_restaurant_config(restaurant_id)
        menu = normalize_item_keys(load_item_mapping(
            config.cross_get(get_config_key(restaurant_id, data['menu_key']), default='{}'),
            config_name=data['menu_key'],
        ))
        capacity = get_restaurant_capacity(config, restaurant_id)
        print(f'  {data["name"]}：{len(menu)} 道菜，单菜货架容量 {capacity}')
        for item_id, amount in sorted(menu.items()):
            dishes[item_id] = dishes.get(item_id, 0) + amount
            print(f'    - {name(item_id)} ({item_id})：{amount:g}')
    expanded = expand_items(dishes)
    raw = {item_id: amount for item_id, amount in expanded.items() if amount > 0}
    print(f'\n  一个周期共消耗 {len(raw)} 种物品（含原料展开）：')
    for item_id, amount in sorted(raw.items(), key=lambda kv: -kv[1]):
        print(f'    {name(item_id)} ({item_id}): {amount:.1f}')


def print_free_band(config):
    print()
    print('=' * 78)
    print('[2] 订单 / 货运委托 — 随机扫描，无静态需求，只能吃“空闲带”')
    print('=' * 78)
    buffer_items = load_buffer(config)
    hard_floor = normalize_item_keys(load_hard_floor_items(
        config.cross_get('IslandProduction.IslandProduction.HardFloorItems', '') or ''
    ))
    reserve = get_menu_reserve_items(config)
    band = get_free_band(config, buffer_items, hard_floor)
    claimed = {item_id: width - band[item_id] for item_id, width in buffer_items.items()}
    print('  被菜单预留完全占满（订单/货运拿不到）：')
    blocked = [item_id for item_id, free in band.items() if free <= 0]
    print(f'    {len(blocked)} 种：' + '、'.join(name(item_id) for item_id in sorted(blocked)))
    print('\n  还有空闲带（缓冲宽度 - 菜单预留 - 硬下限）：')
    for item_id, free in sorted(band.items(), key=lambda kv: -kv[1]):
        if free <= 0:
            continue
        print(f'    {name(item_id)} ({item_id}): 缓冲 {buffer_items[item_id]:g} '
              f'- 预留 {claimed[item_id]:g} = 空闲 {free:g}')


def print_season(config):
    print()
    print('=' * 78)
    print('[3] 开发季 — 当季任务目标（整季需求量，不是每日流量）')
    print('=' * 78)
    season_id, season = get_active_season(config)
    if not season:
        print('  当前没有进行中的开发季')
        return
    print(f'  当前季 {season_id}（第 {season.get("id", 0)} 季），{len(season["task_list"])} 条任务')
    for task_id in season['task_list']:
        task = DIC_ISLAND_TASK.get(task_id, {})
        target = task.get('target') or {}
        if not target:
            continue
        detail = '、'.join(f'{name(item_id)}×{amount}' for item_id, amount in sorted(target.items()))
        print(f'    {task.get("name", {}).get(server.server, task_id)} ({task_id}): {detail}')
    totals = get_season_task_items(config)
    expanded = expand_items(totals)
    print(f'\n  任务目标 {len(totals)} 种物品，展开原料后 {len(expanded)} 种：')
    for item_id, amount in sorted(expanded.items(), key=lambda kv: -kv[1]):
        direct = totals.get(item_id)
        suffix = f'（任务直接要求 {direct:g}）' if direct else ''
        print(f'    {name(item_id)} ({item_id}): {amount:.1f}{suffix}')
    delivery = get_season_delivery_items(config)
    extra = {item_id: amount for item_id, amount in delivery.items() if item_id not in totals}
    if extra:
        print('\n  卡单季订单追加：')
        for item_id, amount in sorted(extra.items()):
            print(f'    {name(item_id)} ({item_id}): {amount:g}')


def print_idle(config):
    print()
    print('=' * 78)
    print('[4] 闲置累积 — 每日累积速度（空槽时的填充量）')
    print('=' * 78)
    idle = normalize_item_keys(load_item_mapping(
        config.cross_get('IslandProduction.IslandProduction.IdleAccumulatingItems', '') or '',
        config_name='IdleAccumulatingItems',
    ))
    for item_id, rate in sorted(idle.items(), key=lambda kv: -kv[1]):
        print(f'    {name(item_id)} ({item_id}): {rate:.1f}/天')


def print_tiers(config, tier_filter=None):
    print()
    print('=' * 78)
    print('[5] 生产优先级 — 每个物品归到哪一层')
    print('=' * 78)
    ledger = build_demand_ledger(config)
    for tier in (TIER_MENU, TIER_SURPLUS, TIER_SEASON, TIER_IDLE):
        if tier_filter is not None and tier != tier_filter:
            continue
        # Only recipe products can be ordered in the sequence, raw materials and
        # seeds are bought or gathered.
        items = sorted(
            item_id for item_id in ledger
            if item_id in RECIPE_PRODUCTS and get_priority_tier(item_id, ledger) == tier
        )
        if not items:
            continue
        print(f'  T{tier} {TIER_NAMES[tier]}（{len(items)} 种）')
        for item_id in items:
            demand = ledger[item_id]
            amounts = '，'.join(f'{source} {amount:.0f}' for source, amount in sorted(demand.items()))
            print(f'    {name(item_id)} ({item_id}): {amounts}')


def main():
    tier_filter = None
    if '--tier' in sys.argv:
        tier_filter = int(sys.argv[sys.argv.index('--tier') + 1])
    config = AzurLaneConfig('alas')
    if tier_filter is None or tier_filter == TIER_MENU:
        print_restaurants(config)
    if tier_filter is None or tier_filter == TIER_SURPLUS:
        print_free_band(config)
    if tier_filter is None or tier_filter == TIER_SEASON:
        print_season(config)
    if tier_filter is None or tier_filter == TIER_IDLE:
        print_idle(config)
    print_tiers(config, tier_filter)


if __name__ == '__main__':
    main()
