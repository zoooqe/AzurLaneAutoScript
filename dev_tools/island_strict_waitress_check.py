"""Offline checks for the strict waitress mode of IslandRestaurant.

Strict mode never puts a substitute on a slot: when a configured waitress is
not on duty, the round is skipped instead. These checks replace the dock UI
with small stubs so choose_waitress can run without a device.

Run with: toolkit/python.exe dev_tools/island_strict_waitress_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.island_handler import restaurant as restaurant_module
from module.island_handler.restaurant import IslandRestaurant, WaitressUnavailable


class Candidate:
    def __init__(self, identity, status):
        self.identity = identity
        self.status = status
        self.button = 'CARD_' + identity


class Config:
    def __init__(self, strict):
        self.strict = strict

    def cross_get(self, key, default=None):
        if key.endswith('StrictWaitress'):
            return self.strict
        return default


def make(strict, dock):
    """dock: identity -> status, or None when the character is missing."""
    obj = object.__new__(IslandRestaurant)
    obj.working_restaurant_id = 901
    obj.config = Config(strict)
    # cached_property reads __dict__ first; without it the offset chain would
    # reach self.device, which does not exist on a stub.
    obj.__dict__['_restaurant_offset'] = (0, 0, 0, 0)
    obj.selected = []
    obj.fallback_calls = []
    obj.loop = lambda **kwargs: iter([0])
    obj.appear_then_click = lambda *args, **kwargs: False
    obj.is_in_island_dock = lambda: True
    obj.unavailable_waitress_list = lambda: []
    obj.ui_back = lambda **kwargs: None
    obj.ensure_dock_page_at_top = lambda: None
    obj.island_dock_select_confirm = lambda **kwargs: True
    obj.island_dock_select_one = lambda button: obj.selected.append(button)
    obj.island_dock_find_character = lambda identity: (
        None if dock.get(identity) is None else Candidate(identity, dock[identity])
    )

    def fallback(blacklist):
        obj.fallback_calls.append(sorted(blacklist))
        return Candidate('Someone_Else', 'free')

    obj.island_dock_find_character_with_blacklist = fallback
    return obj


restaurant_module.get_waitress_slots = (
    lambda config, restaurant_id: ('Cheshire', 'Belfast')
)
restaurant_module.production_place_name_with_id = lambda rid: 'Cafe'

results = []


def check(name, actual, expected):
    status = 'OK  ' if actual == expected else 'FAIL'
    print(f'[{status}] {name}: {actual!r} (expected {expected!r})')
    results.append(actual == expected)


# 1. Strict off, she is busy -> old behaviour, a substitute is picked.
obj = make(False, {'Cheshire': 'occupied', 'Belfast': 'free'})
ok = obj.choose_waitress()
check('strict off, busy -> fallback used', (ok, len(obj.fallback_calls) > 0), (True, True))

# 2. Strict on, she is busy -> skip the round, no substitute at all.
obj = make(True, {'Cheshire': 'occupied', 'Belfast': 'free'})
raised = None
try:
    obj.choose_waitress()
except WaitressUnavailable as e:
    raised = str(e)
check('strict on, busy -> WaitressUnavailable', bool(raised), True)
check('strict on, busy -> no fallback', obj.fallback_calls, [])
check('strict on, busy -> button untouched', obj.selected, [])

# 3. Strict on, both on duty -> both get picked, still no fallback.
obj = make(True, {'Cheshire': 'free', 'Belfast': 'free'})
ok = obj.choose_waitress()
check('strict on, both free -> both picked', (ok, obj.selected), (True, ['CARD_Cheshire', 'CARD_Belfast']))
check('strict on, both free -> no fallback', obj.fallback_calls, [])

# 4. Strict on, the character cannot be found in the dock -> skip as well.
obj = make(True, {'Cheshire': None, 'Belfast': 'free'})
raised = None
try:
    obj.choose_waitress()
except WaitressUnavailable as e:
    raised = str(e)
check('strict on, missing character -> WaitressUnavailable', bool(raised), True)
check('strict on, missing character -> no fallback', obj.fallback_calls, [])

# 5. Strict on, the second slot is busy -> the whole round is skipped.
obj = make(True, {'Cheshire': 'free', 'Belfast': 'occupied'})
raised = None
try:
    obj.choose_waitress()
except WaitressUnavailable as e:
    raised = str(e)
check('strict on, second busy -> WaitressUnavailable', bool(raised), True)
check('strict on, second busy -> no fallback', obj.fallback_calls, [])

print()
print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
