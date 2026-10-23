"""Offline check: the collect round gives up instead of dying on a dead button.

The 2026-10-25 18:03 run sent a character with 86 emotion, every click on the
start button was silently ignored and the round ended as a GameTooManyClickError
with a human takeover request. Crew selection now lives in the dock layer
(island_dock_select_character), so what is left to guard here is the click limit.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.island.collect import COLLECT_START_CLICK_LIMIT

results = []


def check(name, actual, expected):
    ok = actual == expected
    results.append(ok)
    print(f'[{"OK" if ok else "FAIL"}] {name}')
    if not ok:
        print(f'   actual  : {actual}')
        print(f'   expected: {expected}')


# Device.click_record_check() trips at 12 clicks on one button, or 6 on each of
# two, so the retry limit has to stay below both.
check('retry limit stays under stuck detection', COLLECT_START_CLICK_LIMIT < 6, True)
# A working round needs two or three clicks, so the limit must not cut those off.
check('retry limit still allows a normal round', COLLECT_START_CLICK_LIMIT >= 3, True)

print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
