"""Pure helpers behind the island item picker in the web UI.

The island tasks store item amounts as YAML text in config, for example::

    IslandProduction.DailyBufferItems:
        小麦 (2000): 300

This module converts between that text and editable rows, so the GUI can offer
a picker with amounts instead of a raw textarea. There is no device, config
instance or UI dependency here, which keeps it unit testable.
"""
from typing import Dict, List, Optional, Tuple

from module.island.data import DIC_ISLAND_ITEM
from module.island.utils import (
    item_mapping_to_yaml,
    load_item_mapping,
    normalize_item_id,
)

# Amounts are planned as float, 3 decimals is already beyond game precision.
AMOUNT_PRECISION = 3


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
