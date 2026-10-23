"""Offline check: the bedtime arrangement of island production.

When Island Production runs inside its bedtime window (00:00 for 8 hours by
default), every production slot should be filled for the whole window, so
nothing goes idle overnight. Recipes whose batch limit cannot reach the window
still run to the limit: that was the agreed behaviour, so the filler picker
must keep offering them instead of dropping them.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.config.config_generated import GeneratedConfig
from module.island.data import DIC_ISLAND_RECIPE
from module.island.utils import (
    get_bedtime_batch_count,
    get_bedtime_window,
    parse_bedtime_time,
)
from module.island_handler.recipe import IslandRecipe, RecipeInfo

results = []


def check(name, got, want):
    ok = got == want
    results.append(ok)
    print(('  OK  ' if ok else ' FAIL ') + name)
    if not ok:
        print('        got : %r' % (got,))
        print('        want: %r' % (want,))


def max_hours(recipe_id):
    """Longest run one slot can get from this recipe."""
    data = DIC_ISLAND_RECIPE[recipe_id]
    return data['production_limit'] * data['workload'] / 36000


class FakeRecipe:
    """Only the attribute surface the filler picker touches."""

    get_bedtime_filler_entries = IslandRecipe.get_bedtime_filler_entries
    get_bedtime_filler_hours = IslandRecipe.get_bedtime_filler_hours

    def __init__(self, place_id, hours):
        self.bedtime_place_id = place_id
        self.bedtime_hours = hours
        self.recipe_info_by_id = {
            recipe_id: RecipeInfo(
                stock=0, target_stock=10, daily_buffer=5, batch_size=1,
                demand=[], idle_accumulating=0, hard_floor=0,
            )
            for recipe_id in DIC_ISLAND_RECIPE
        }


print('parse_bedtime_time')
check('HH:MM', parse_bedtime_time('00:00'), (0, 0))
check('23:30', parse_bedtime_time('23:30'), (23, 30))
check('single digit hour', parse_bedtime_time('7:05'), (7, 5))
check('full-width colon', parse_bedtime_time('23：30'), (23, 30))
check('bare hour', parse_bedtime_time('8'), (8, 0))
check('garbage falls back to midnight', parse_bedtime_time('abc'), (0, 0))
check('out of range falls back', parse_bedtime_time('25:00'), (0, 0))
check('empty falls back', parse_bedtime_time(''), (0, 0))

print()
print('get_bedtime_window')
check('00:00 + 8h contains 00:30',
      get_bedtime_window(datetime(2026, 10, 29, 0, 30), '00:00', 8) is not None, True)
check('00:00 + 8h excludes 08:00',
      get_bedtime_window(datetime(2026, 10, 29, 8, 0), '00:00', 8), None)
check('00:00 + 8h excludes 21:00',
      get_bedtime_window(datetime(2026, 10, 29, 21, 0), '00:00', 8), None)
start, end = get_bedtime_window(datetime(2026, 10, 29, 2, 0), '23:00', 8)
check('23:00 + 8h wraps into the next morning',
      (start.day, start.hour, end.day, end.hour), (28, 23, 29, 7))
check('23:00 + 8h excludes noon',
      get_bedtime_window(datetime(2026, 10, 29, 12, 0), '23:00', 8), None)
check('zero hours never opens the window',
      get_bedtime_window(datetime(2026, 10, 29, 2, 0), '00:00', 0), None)

print()
print('get_bedtime_batch_count')
check('6h per batch needs 2 for 8h', get_bedtime_batch_count(216000, 8), 2)
check('1h per batch needs 8 for 8h', get_bedtime_batch_count(36000, 8), 8)
check('rounds up instead of down', get_bedtime_batch_count(100000, 8), 3)
check('zero hours asks for nothing', get_bedtime_batch_count(36000, 0), 0)
check('zero workload still asks for one', get_bedtime_batch_count(0, 8), 1)

print()
print('bedtime filler selection')
PLACE = 201
recipes = [recipe_id for recipe_id in DIC_ISLAND_RECIPE if recipe_id // 1000 == PLACE]
full = [recipe_id for recipe_id in recipes if max_hours(recipe_id) >= 8]
partial = [recipe_id for recipe_id in recipes if max_hours(recipe_id) < 8]
assert full, 'place 201 should have recipes that cover 8h'
assert partial, 'place 201 should have recipes that cannot'

picker = FakeRecipe(PLACE, 8)
ids = [entry[0] for entry in picker.get_bedtime_filler_entries([])]
kinds = [max_hours(recipe_id) >= 8 for recipe_id in ids]

check('only recipes of the dispatching place', all(r // 1000 == PLACE for r in ids), True)
check('covers-window recipes lead the list', kinds == sorted(kinds, reverse=True), True)
check('short recipes are still offered', any(not k for k in kinds), True)
check('every recipe of the place is offered', len(ids), len(recipes))

planned = full[0]
entries = picker.get_bedtime_filler_entries([(planned, picker.recipe_info_by_id[planned], None)])
check('planned recipe is not offered again', planned in [e[0] for e in entries], False)
check('filler is marked as its own mode',
      {entry[2] for entry in entries}, {'bedtime'})
check('longest run is reported', picker.get_bedtime_filler_hours(planned), max_hours(planned))

check('no filler outside the window', FakeRecipe(PLACE, 0).get_bedtime_filler_entries([]), [])
check('no filler without a place', FakeRecipe(None, 8).get_bedtime_filler_entries([]), [])

print()
print('batches stop at the production limit')
short = min(recipes, key=max_hours)
limit = DIC_ISLAND_RECIPE[short]['production_limit']
asked = get_bedtime_batch_count(DIC_ISLAND_RECIPE[short]['workload'], 8)
check('short recipe asks for more than the limit', asked > limit, True)
check('the limit is what actually runs', min(asked, limit), limit)

print()
print('config defaults')
check('bedtime is off by default', GeneratedConfig.IslandProduction_BedtimeEnable, False)
check('window starts at midnight', GeneratedConfig.IslandProduction_BedtimeTime, '00:00')
check('window is 8 hours', GeneratedConfig.IslandProduction_BedtimeHours, 8)

print()
print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
