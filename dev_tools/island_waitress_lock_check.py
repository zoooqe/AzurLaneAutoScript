"""Offline checks for the waitress lock decision of IslandRestaurant.

The lock now depends on three questions:
  1. is the waitress really employed by another running restaurant,
  2. do both restaurants still need a shift today,
  3. does another full shift still fit before the daily reset.

Run with: toolkit/python.exe dev_tools/island_waitress_lock_check.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.island_handler import restaurant as restaurant_module
from module.island_handler.restaurant import IslandRestaurant

NOW = datetime(2026, 10, 23, 10, 0)
NEXT_UPDATE = datetime(2026, 10, 24, 0, 0)

# Prinz_Eugen is assigned to both 603 (Eatery) and 604 (Grill).
SLOTS = {
    601: ('none', 'none'),
    602: ('none', 'none'),
    603: ('Prinz_Eugen', 'none'),
    604: ('Prinz_Eugen', 'none'),
    901: ('none', 'none'),
}

restaurant_module.get_waitress_slots = lambda config, restaurant_id: SLOTS[restaurant_id]
restaurant_module.get_server_next_update = lambda target: NEXT_UPDATE


def make(states, finish_times):
    obj = object.__new__(IslandRestaurant)
    obj.working_restaurant_id = 604
    obj.restaurant_states = states
    obj.restaurant_finish_times = finish_times
    obj.config = object()
    return obj


def check(name, actual, expected):
    status = 'OK  ' if actual == expected else 'FAIL'
    print(f'[{status}] {name}: {actual!r} (expected {expected!r})')
    return actual == expected


results = []

# 1. She is on shift and the next shift still fits before 00:00 -> wait for her.
obj = make({603: 'running'}, {603: NOW + timedelta(hours=4)})
status, employer, finish = obj.locked_waitress_status('Prinz_Eugen')
results.append(check('running shift, ends 14:00', (status, employer, finish), ('running', 603, NOW + timedelta(hours=4))))
results.append(check('14:00 + 8h fits before 00:00 -> lock', obj.should_lock_waitress(finish), True))

# 2. She is on shift, but a second shift would end after 00:00 -> replace her.
obj = make({603: 'running'}, {603: NOW + timedelta(hours=8)})
_, _, finish = obj.locked_waitress_status('Prinz_Eugen')
results.append(check('18:00 + 8h overflows -> no lock', obj.should_lock_waitress(finish), False))

# 3. Exactly at the boundary (16:00 + 8h == 00:00) still fits.
obj = make({603: 'running'}, {603: NOW + timedelta(hours=6)})
_, _, finish = obj.locked_waitress_status('Prinz_Eugen')
results.append(check('16:00 + 8h == 00:00 -> lock', obj.should_lock_waitress(finish), True))

# 4. Her shift already crosses 00:00 -> always wait, never open a late-night round.
obj = make({603: 'running'}, {603: NEXT_UPDATE + timedelta(hours=1)})
_, _, finish = obj.locked_waitress_status('Prinz_Eugen')
results.append(check('shift ends tomorrow 01:00 -> lock', obj.should_lock_waitress(finish), True))

# 5. The other restaurant is resting today -> she is free, pick her normally.
obj = make({603: 'resting'}, {})
results.append(check('other restaurant resting', obj.locked_waitress_status('Prinz_Eugen'), ('idle', None, None)))

# 6. The other restaurant has not opened yet today -> she is free as well.
obj = make({603: 'ready'}, {})
results.append(check('other restaurant ready', obj.locked_waitress_status('Prinz_Eugen'), ('idle', None, None)))

# 7. Unknown state (asset recognition failed) -> keep the old, safe behaviour.
obj = make({}, {})
results.append(check('unknown state', obj.locked_waitress_status('Prinz_Eugen'), ('unknown', None, None)))

# 8. She is not assigned to any other restaurant -> nothing to lock.
obj = make({603: 'running'}, {603: NOW + timedelta(hours=4)})
results.append(check('not shared with 603', obj.locked_waitress_status('Belfast'), ('idle', None, None)))

print()
print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
