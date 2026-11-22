"""Pure helpers behind the island item picker in the web UI.

The island tasks store item amounts as YAML text in config, for example::

    IslandProduction.DailyBufferItems:
        小麦 (2000): 300

This module converts between that text and editable rows, so the GUI can offer
a picker with amounts instead of a raw textarea. There is no device, config
instance or UI dependency here, which keeps it unit testable.
"""
from typing import Dict, List, Optional, Tuple

from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE
from module.island.utils import (
    item_mapping_to_yaml,
    load_item_mapping,
    normalize_item_id,
)

# Amounts are planned as float, 3 decimals is already beyond game precision.
AMOUNT_PRECISION = 3

# {item_id} of everything a production recipe outputs, filled on first use.
_PRODUCIBLE_ITEM_IDS = None


def _item_names(item_id) -> Dict[str, str]:
    item = DIC_ISLAND_ITEM.get(int(item_id))
    if not item:
        return {}
    return item.get('name', {})


def item_name_of(item_id, lang: str = 'cn') -> str:
    """Item name in the given server language, fallback to cn, then to the id."""
    names = _item_names(item_id)
    return names.get(lang) or names.get('cn') or str(item_id)


def item_label(item_id, lang: str = 'cn') -> str:
    """Label used both in the picker and as the config key, e.g. `小麦 (2000)`."""
    return f'{item_name_of(item_id, lang)} ({int(item_id)})'


def item_options(lang: str = 'cn') -> List[Tuple[str, str]]:
    """[(value, label)] sorted by item id, ready for put_select()."""
    return [(str(item_id), item_label(item_id, lang)) for item_id in sorted(DIC_ISLAND_ITEM)]


def parse_amount(raw) -> Optional[float]:
    """Convert user input to a non-negative amount, None when invalid."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    else:
        text = str(raw).strip().replace(',', '').replace('，', '')
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            return None
    if value != value or value in (float('inf'), float('-inf')):
        return None
    if value < 0:
        return None
    return round(value, AMOUNT_PRECISION)


def format_amount(amount) -> str:
    """Human readable amount, `300` for integers and `300.5` for decimals."""
    value = parse_amount(amount)
    if value is None:
        return ''
    if float(value).is_integer():
        return str(int(value))
    return f'{value:.{AMOUNT_PRECISION}f}'.rstrip('0').rstrip('.')


def items_from_text(text) -> Dict[int, float]:
    """Parse config YAML text into {item_id: amount}.

    Malformed text is treated as empty instead of raising, so a broken config
    can still be opened and repaired in the GUI. Unknown entries are skipped
    one by one, the rest of the mapping is still kept.
    """
    if not text:
        return {}
    try:
        mapping = load_item_mapping(text, config_name='IslandItems')
    except Exception:
        return {}
    if not isinstance(mapping, dict):
        return {}

    result = {}
    for raw_key, raw_amount in mapping.items():
        try:
            item_id = normalize_item_id(raw_key)
        except (ValueError, KeyError, TypeError):
            continue
        amount = parse_amount(raw_amount)
        if amount is not None:
            result[int(item_id)] = amount
    return result


def is_valid_item_id(item_id) -> bool:
    try:
        return int(item_id) in DIC_ISLAND_ITEM
    except (TypeError, ValueError):
        return False


def items_to_text(items) -> str:
    """Serialise {item_id: amount} back to the same YAML format the tasks read."""
    payload = {}
    for item_id, amount in (items or {}).items():
        if not is_valid_item_id(item_id):
            continue
        value = parse_amount(amount)
        if value is None:
            continue
        # Keep whole numbers as int, same as the planner export.
        payload[int(item_id)] = int(value) if float(value).is_integer() else value
    if not payload:
        return '{}'
    return item_mapping_to_yaml(payload, use_item_name=True)


def set_item(items: Dict[int, float], item_id, amount) -> Dict[int, float]:
    """Add or update an item, drop it when amount is not positive."""
    if not is_valid_item_id(item_id):
        return items
    value = parse_amount(amount)
    item_id = int(item_id)
    if value is None or value <= 0:
        items.pop(item_id, None)
    elif float(value).is_integer():
        items[item_id] = int(value)
    else:
        items[item_id] = value
    return items


def sorted_items(items) -> List[Tuple[int, float]]:
    return [(item_id, items[item_id]) for item_id in sorted(items or {})]


def producible_items() -> List[int]:
    """Item ids some production recipe outputs, sorted by id.

    Items only obtainable from the shop, exchange or gathering have no recipe,
    so they can never be accumulated by idle production and are not offered in
    the idle accumulating picker.
    """
    return sorted(_producible_item_ids())


def _producible_item_ids():
    global _PRODUCIBLE_ITEM_IDS
    if _PRODUCIBLE_ITEM_IDS is None:
        from module.island.data import DIC_ISLAND_RECIPE

        items = set()
        for recipe in DIC_ISLAND_RECIPE.values():
            for item_id, amount in (recipe.get('commission_product') or {}).items():
                if amount > 0:
                    items.add(int(item_id))
        _PRODUCIBLE_ITEM_IDS = frozenset(items)
    return _PRODUCIBLE_ITEM_IDS


def idle_accumulating_options(lang: str = 'cn') -> List[Tuple[str, str]]:
    """[(value, label)] of the items idle production can accumulate."""
    return [(str(item_id), item_label(item_id, lang)) for item_id in producible_items()]


def is_producible_item(item_id) -> bool:
    try:
        return int(item_id) in _producible_item_ids()
    except (TypeError, ValueError):
        return False


def idle_accumulating_amount(item_id, dispatch_hours=None) -> Optional[float]:
    """Expected daily accumulation rate written for an idle item.

    The config value is not a stock amount: it is the rate used to weigh how
    fast a surplus of that item is burned down, and the planner normally fills
    it with the leftover production of the day.

    For an item the planner would not pick there is no planned rate, so the
    amount one idle dispatch produces is written instead: the batches that fit
    in `ISLAND_IDLE_ACCUMULATING_DISPATCH_HOURS` of workload, times the batch
    output. That is exactly what the task commits when it runs the recipe in
    idle accumulating mode.
    """
    from module.island.data import DIC_ISLAND_RECIPE
    from module.island.utils import get_idle_accumulating_batch_count

    if dispatch_hours is None:
        try:
            from module.config.config_manual import ManualConfig

            dispatch_hours = ManualConfig.ISLAND_IDLE_ACCUMULATING_DISPATCH_HOURS
        except Exception:
            dispatch_hours = 6

    try:
        item_id = int(item_id)
    except (TypeError, ValueError):
        return None

    for recipe in DIC_ISLAND_RECIPE.values():
        outputs = recipe.get('commission_product') or {}
        if outputs.get(item_id, 0) <= 0:
            continue
        batches = get_idle_accumulating_batch_count(recipe['workload'], dispatch_hours)
        return parse_amount(batches * outputs[item_id])
    return None


def restaurant_id_of_menu(menu_key) -> Optional[int]:
    """Map a menu config key such as ``KoiMenu`` back to its restaurant id."""
    from module.island_handler.restaurant_config import RESTAURANT_CONFIG

    for restaurant_id, config in RESTAURANT_CONFIG.items():
        if config['menu_key'] == menu_key:
            return restaurant_id
    return None


def menu_item_options(restaurant_id, lang: str = 'cn') -> List[Tuple[str, str]]:
    """[(value, label)] of the dishes this restaurant is able to serve.

    The menu of a restaurant is fixed by the game data, dishes of another
    restaurant cannot be put on its shelves, so the picker offers only these.
    """
    try:
        restaurant_id = int(restaurant_id)
    except (TypeError, ValueError):
        return []
    menu = DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.get(restaurant_id, {})
    return [(str(item_id), item_label(item_id, lang)) for item_id in sorted(menu)]


def is_menu_item(restaurant_id, item_id) -> bool:
    """Whether the dish belongs to this restaurant's menu."""
    try:
        restaurant_id = int(restaurant_id)
        item_id = int(item_id)
    except (TypeError, ValueError):
        return False
    return item_id in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.get(restaurant_id, {})
