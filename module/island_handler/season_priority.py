"""Production priority ladder for the island.

One production slot runs one recipe at a time, so the order of the production
sequence decides which demand is served first. The island asks for items from
four places, in this order:

  1. restaurant menus -- the dishes are what earns the coins, they run first.
     Demand is known: every dish on a menu sells a whole shelf tranche per
     business cycle, and its recipe chain is expanded down to raw materials.
  2. orders and transport commissions -- they ask for random sellable items and
     there is no static table for them (only season orders have one), so they
     cannot be planned for by item. They take whatever stock is left above the
     hard floor and the menu reserve, so the buffer surplus above the menu
     reserve is what feeds them. See `get_free_band`.
  3. development season -- fixed amounts over a whole season, and the season
     lasts months. The tasks are expanded and produced in the tail of the
     sequence, after the menus and after the surplus that orders live on.
  4. idle accumulation -- filler for slots that would otherwise idle, last.

Demoting never drops a recipe: it only moves it behind the higher priority
entries, so it still runs once the higher ones are satisfied.
"""

from collections import Counter

import module.config.server as server
from module.island.data import (
    DIC_ISLAND_EXCHANGE_RECIPE,
    DIC_ISLAND_ITEM,
    DIC_ISLAND_RECIPE,
    DIC_ISLAND_SHOP_RECIPE,
    DIC_ISLAND_TASK,
)
from module.island.season_progress import get_active_season, get_completed_task_ids
from module.island.utils import (
    get_stuck_season_order_requirements,
    load_item_mapping,
    normalize_item_keys,
    resolve_stuck_season_order_id,
)
from module.island_handler.restaurant_config import (
    RESTAURANT_IDS,
    get_config_key,
    get_menu_reserve_items,
    get_restaurant_config,
)
from module.logger import logger

# Tiers of the ladder above, highest priority first. Orders and transport share
# tier 1: they consume the same surplus and differ only in when they are
# submitted, not in what production has to make.
TIER_MENU = 0
TIER_SURPLUS = 1
TIER_SEASON = 2
TIER_IDLE = 3

TIER_NAMES = {
    TIER_MENU: 'menu',
    TIER_SURPLUS: 'order/transport',
    TIER_SEASON: 'season',
    TIER_IDLE: 'idle',
}


def _build_producers(tables):
    """{item_id: (batch_output, inputs)}, the recipe with the largest batch wins."""
    producers = {}
    for table in tables:
        for recipe in table.values():
            outputs = recipe.get('commission_product') or recipe.get('items') or {}
            costs = recipe.get('commission_cost') or recipe.get('resource_consume') or {}
            for item_id, amount in outputs.items():
                if amount <= 0 or not costs:
                    continue
                if item_id not in producers or amount > producers[item_id][0]:
                    producers[item_id] = (amount, costs)
    return producers


# Shop and exchange recipes only trace raw materials back: feed is milled from
# wheat and flour from wheat, so they have to be followed even though the island
# task never runs them as production recipes.
PRODUCERS = _build_producers([DIC_ISLAND_RECIPE, DIC_ISLAND_SHOP_RECIPE, DIC_ISLAND_EXCHANGE_RECIPE])
RECIPE_PRODUCTS = frozenset(_build_producers([DIC_ISLAND_RECIPE]))


def expand_items(needs, max_depth=12):
    """Total material consumption of producing needs, level by level."""
    total = Counter()
    level = Counter(needs)
    for _ in range(max_depth):
        if not level:
            break
        following = Counter()
        for item_id, amount in level.items():
            total[item_id] += amount
            producer = PRODUCERS.get(item_id)
            if not producer:
                continue
            batch_output, costs = producer
            batches = amount / batch_output
            for ingredient, quantity in costs.items():
                following[ingredient] += quantity * batches
        level = following
    return total


def get_active_season_id(config=None):
    """Namespace of the configured development season, 0 if none is."""
    season_id, _ = get_active_season(config)
    return season_id


def get_completed_season_tasks(config=None):
    """Task ids of the configured season that are already handed in."""
    return get_completed_task_ids(config)


def get_season_task_items(config=None, completed_ids=None):
    """Item amounts the remaining tasks of the configured season ask for.

    Delivered tasks are skipped: the record in `season_progress` keeps their
    amounts out of the buffer and out of the priority ladder, so the island
    stops producing for work that is already done.
    """
    items = Counter()
    season_id, season = get_active_season(config)
    if not season:
        return items
    completed = get_completed_task_ids(config) if completed_ids is None else set(completed_ids)
    for task_id in season['task_list']:
        if task_id in completed:
            continue
        items.update(DIC_ISLAND_TASK.get(task_id, {}).get('target') or {})
    return +items


def get_menu_items(config):
    """Item amounts the configured restaurant menus sell in one business cycle."""
    items = Counter()
    for restaurant_id in RESTAURANT_IDS:
        config_data = get_restaurant_config(restaurant_id)
        menu = normalize_item_keys(load_item_mapping(
            config.cross_get(get_config_key(restaurant_id, config_data['menu_key']), default='{}'),
            config_name=config_data['menu_key'],
        ))
        items.update(menu)
    return +items


def get_season_delivery_items(config):
    """Item amounts the development season still has to be delivered."""
    items = get_season_task_items(config)
    _, season = get_active_season(config)
    order_id = resolve_stuck_season_order_id(
        config.cross_get('IslandOrder.IslandOrder.StuckSeasonOrderId', 0),
        season,
    )
    items.update(get_stuck_season_order_requirements(order_id))
    return +items


def get_idle_items(config):
    """{item_id: items per day} configured as idle accumulation weights."""
    return normalize_item_keys(load_item_mapping(
        config.cross_get('IslandProduction.IslandProduction.IdleAccumulatingItems', '') or '',
        config_name='IdleAccumulatingItems',
    ))


def build_demand_ledger(config):
    """{item_id: {source: amount}}, the demand of every sub task by item.

    Sources:
        menu: one business cycle of the configured dishes, raw materials
            included, so it is the amount the restaurants consume per cycle.
        season: what the remaining development season tasks and the stuck
            season order still ask for, raw materials included.
        idle: the daily accumulation rate configured for the item.

    Orders and transport commissions have no entry: their items are scanned from
    the screen at runtime and change with every refresh, so there is no amount to
    plan against. `get_free_band` describes what they may consume instead.
    """
    ledger = {}

    def add(source, amounts):
        for item_id, amount in amounts.items():
            # Development funds and PT are spent, never produced.
            if item_id <= 1 or amount <= 0:
                continue
            ledger.setdefault(item_id, {})[source] = amount

    add('menu', expand_items(get_menu_items(config)))
    add('season', expand_items(get_season_delivery_items(config)))
    add('idle', get_idle_items(config))
    return ledger


def get_priority_tier(item_id, ledger):
    """Tier of an item: the highest priority sub task that asks for it."""
    demand = ledger.get(item_id)
    if not demand:
        return TIER_SURPLUS
    if demand.get('menu', 0) > 0:
        return TIER_MENU
    if demand.get('season', 0) > 0:
        return TIER_SEASON
    if demand.get('idle', 0) > 0:
        return TIER_IDLE
    return TIER_SURPLUS


def get_season_only_products(ledger):
    """Recipe products only the development season asks for."""
    return frozenset(
        item_id
        for item_id, demand in ledger.items()
        if demand.get('season', 0) > 0 and demand.get('menu', 0) <= 0
        and item_id in RECIPE_PRODUCTS
    )


def get_low_priority_products(config):
    """Recipe products only the season asks for, produced after everything else."""
    products = get_season_only_products(build_demand_ledger(config))
    if products:
        logger.attr('Season_only_products', ', '.join(str(item_id) for item_id in sorted(products)))
    return products


def get_free_band(config, buffer_items, hard_floor_items=None):
    """Stock orders and transport commissions may consume, per item.

    Orders and transport are turned away while the stock is below the hard floor
    plus the menu reserve, so the free band is what a menu cycle does not need.
    The buffer width is the band production keeps refilled; a band of 0 means the
    item is entirely claimed by the restaurants.
    """
    reserve = get_menu_reserve_items(config)
    hard_floor_items = hard_floor_items or {}
    band = {}
    for item_id, width in buffer_items.items():
        claimed = max(hard_floor_items.get(item_id, 0), 0) + reserve.get(item_id, 0)
        band[item_id] = max(width - claimed, 0)
    return band


def split_by_priority(entries, products):
    """Split the sorted normal entries into (kept, demoted), keeping both orders."""
    if not products:
        return entries, []
    kept = []
    demoted = []
    for entry in entries:
        product_id = next(iter(DIC_ISLAND_RECIPE[entry[0]]['commission_product']))
        if product_id in products:
            demoted.append(entry)
        else:
            kept.append(entry)
    return kept, demoted


def demote_entries(entries, products):
    """Move the season-only entries behind the rest, keeping both orders."""
    if not products:
        return entries
    kept, demoted = split_by_priority(entries, products)
    return kept + demoted


def summarize_demand(ledger):
    """One line per sub task: how many items and how much is asked for."""
    parts = []
    for source in ('menu', 'season', 'idle'):
        amounts = {
            item_id: demand[source]
            for item_id, demand in ledger.items()
            if demand.get(source, 0) > 0
        }
        if not amounts:
            continue
        parts.append(f'{source} {len(amounts)} items ({sum(amounts.values()):.0f} total)')
    return ', '.join(parts) or 'no demand'


def format_demand_ledger(ledger):
    """Multi line report of the demand ledger, ordered by tier then name."""
    lines = []
    for tier in (TIER_MENU, TIER_SURPLUS, TIER_SEASON, TIER_IDLE):
        items = sorted(
            item_id for item_id in ledger if get_priority_tier(item_id, ledger) == tier
        )
        if not items:
            continue
        lines.append(f'[T{tier} {TIER_NAMES[tier]}] {len(items)} items')
        for item_id in items:
            demand = ledger[item_id]
            amounts = ', '.join(
                f'{source} {amount:.0f}' for source, amount in sorted(demand.items())
            )
            name = DIC_ISLAND_ITEM.get(item_id, {}).get('name', {}).get(server.server, str(item_id))
            lines.append(f'  {name} ({item_id}): {amounts}')
    return lines
