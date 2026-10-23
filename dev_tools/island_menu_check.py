"""Offline checks for IslandRestaurant.get_sell_plan() with WaitForFullMenu.

The method depends on the device (it scans the item grid), so the scanner and
the config are stubbed out and the real method is bound to a fake instance.

Cases:
1. full menu, switch on   -> plan keeps every configured dish
2. partial menu, switch on -> MenuIncomplete, listing exactly the short dish
3. partial menu, switch off-> old behaviour, vacant shelves filled with surplus
4. configured dish not on the shelf at all -> counted as short
"""
import sys
from types import MethodType

sys.path.insert(0, '.')

import module.webui.lang  # noqa: F401  (keeps module import order identical)

from module.island_handler.restaurant import IslandRestaurant, MenuIncomplete
import module.island_handler.restaurant as _restaurant_module

# The soft menu reserve only shifts the surplus threshold; not under test here.
_restaurant_module.get_menu_reserve_items = lambda config: {}


class FakeItem:
    def __init__(self, item_id, amount, price=10, tag=None):
        self.id = item_id
        self.amount = amount
        self.price = price
        self.tag = tag
        self.name = 'item{}'.format(item_id)

    def __repr__(self):
        return 'FakeItem({}, {})'.format(self.id, self.amount)


class FakeConfig:
    def __init__(self, menu_text):
        self.menu_text = menu_text

    def cross_get(self, key, default=None):
        if 'Menu' in key:
            return self.menu_text
        if 'Waitress' in key:
            return 'none'
        return default if default is not None else '{}'


class FakeRestaurant:
    working_restaurant_id = 603
    restaurant_capacity = {603: 20}
    restaurant_quantity = {603: 3}
    restaurant_protected_items = {}
    event_buff = 0

    def __init__(self, items, menu_text, wait_for_full_menu):
        self.items = items
        self.config = FakeConfig(menu_text)
        self.wait_for_full_menu = wait_for_full_menu

    def scan_all_items(self):
        return list(self.items)

    def get_sell_plan(self):
        return IslandRestaurant.get_sell_plan(self)


MENU = '{2601: 20, 2602: 20, 2603: 20}'
results = []


def run(name, items, menu_text, switch, expect_error=None, expect_ids=None):
    fake = FakeRestaurant(items, menu_text, switch)
    try:
        plan = fake.get_sell_plan()
        error = None
    except MenuIncomplete as e:
        plan = None
        error = e
    if expect_error is not None:
        ok = error is not None and set(error.missing) == set(expect_error)
        detail = 'missing={}'.format(error.missing if error else 'no error')
    else:
        ids = sorted(item.id for item in plan) if plan is not None else None
        ok = error is None and ids == expect_ids
        detail = 'plan={}'.format(ids)
    results.append(ok)
    print('[{:^4}] {:<34} {}'.format('OK' if ok else 'FAIL', name, detail))


# 1. every dish stocked enough
run('full menu, switch on',
    [FakeItem(2601, 25), FakeItem(2602, 30), FakeItem(2603, 22), FakeItem(2604, 900)],
    MENU, True, expect_ids=[2601, 2602, 2603])

# 2. one dish short, switch on -> refuse to open
run('partial menu, switch on',
    [FakeItem(2601, 25), FakeItem(2602, 30), FakeItem(2603, 5), FakeItem(2604, 900)],
    MENU, True, expect_error=[2603])

# 3. same stock, switch off -> old behaviour fills the vacant shelf
run('partial menu, switch off',
    [FakeItem(2601, 25), FakeItem(2602, 30), FakeItem(2603, 5), FakeItem(2604, 900)],
    MENU, False, expect_ids=[2601, 2602, 2604])

# 4. configured dish absent from the shelf entirely
run('dish not on shelf, switch on',
    [FakeItem(2601, 25), FakeItem(2602, 30), FakeItem(2604, 900)],
    MENU, True, expect_error=[2603])

# 5. empty menu = nothing configured, must not block the round
run('empty menu, switch on',
    [FakeItem(2601, 25), FakeItem(2602, 30)],
    '{}', True, expect_ids=[2601, 2602])

# 6. dish stocked exactly at capacity, no protected extras
run('exact capacity, switch on',
    [FakeItem(2601, 20), FakeItem(2602, 20), FakeItem(2603, 20)],
    MENU, True, expect_ids=[2601, 2602, 2603])

# 7. menu longer than the shelf count: the extra dish cannot take a shelf, so
#    its shortage must not hold the round back (only 3 shelves exist).
run('5 dishes / 3 shelves, 5th short',
    [FakeItem(2601, 25), FakeItem(2602, 25), FakeItem(2603, 25),
     FakeItem(2604, 25), FakeItem(2605, 1), FakeItem(2700, 900)],
    '{2601: 20, 2602: 20, 2603: 20, 2604: 20, 2605: 20}', True,
    expect_ids=[2601, 2602, 2603])

# 8. same shape, but too few ready dishes to cover the shelves -> wait
run('5 dishes / 3 shelves, only 2 ready',
    [FakeItem(2601, 25), FakeItem(2602, 25), FakeItem(2603, 1),
     FakeItem(2604, 1), FakeItem(2605, 1), FakeItem(2700, 900)],
    '{2601: 20, 2602: 20, 2603: 20, 2604: 20, 2605: 20}', True,
    expect_error=[2603, 2604, 2605])

# 9. same shape, two dishes short: the extra menu entries cover the shelves, so
#    the round opens instead of being delayed for a dish that cannot fit anyway.
run('5 dishes / 3 shelves, backups cover it',
    [FakeItem(2601, 1, 30), FakeItem(2602, 1, 26), FakeItem(2603, 25, 20),
     FakeItem(2604, 25, 18), FakeItem(2605, 25, 16)],
    '{2601: 20, 2602: 20, 2603: 20, 2604: 20, 2605: 20}', True,
    expect_ids=[2603, 2604, 2605])

print()
print('{}/{} checks passed'.format(sum(results), len(results)))
sys.exit(0 if all(results) else 1)
