"""Offline check: which crew the collect task picks first.

The 2026-10-25 18:03 run sorted only by adaptability and sent a character with
86 emotion, so every click on the start button was silently ignored and the
round ended as a GameTooManyClickError.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.island.collect import (
    COLLECT_MIN_EMOTION,
    COLLECT_START_CLICK_LIMIT,
    collect_priority,
)
from module.island_handler.dock_scanner import Character


def make(identity, grade, emotion):
    return Character(identity=identity, grade=grade, emotion=emotion)


def order(characters):
    return [c.identity for c in sorted(characters, key=collect_priority)]


results = []


def check(name, actual, expected):
    ok = actual == expected
    results.append(ok)
    print(f'[{"OK" if ok else "FAIL"}] {name}')
    if not ok:
        print(f'   actual  : {actual}')
        print(f'   expected: {expected}')


# The exact 2026-10-25 case: a tired A grade must not jump ahead of a rested B.
crew = [make('Royal_Fortune', 'A', 86), make('Navigator', 'B', 120)]
check('tired A loses to rested B', order(crew), ['Navigator', 'Royal_Fortune'])

# Among rested crew the better adaptability grade still wins.
crew = [make('Laffey', 'B', 150), make('Takao', 'A', 100)]
check('rested crew ordered by grade', order(crew), ['Takao', 'Laffey'])

# Same grade, both rested: the fuller one goes first.
crew = [make('Shimakaze', 'A', 110), make('Takao', 'A', 150)]
check('same grade, more emotion first', order(crew), ['Takao', 'Shimakaze'])

# Everyone tired: keep grade order, the caller warns and limits the retry.
crew = [make('Hood', 'C', 20), make('Royal_Fortune', 'A', 86)]
check('all tired keeps grade order', order(crew), ['Royal_Fortune', 'Hood'])

# Unknown grade but rested still beats a tired known grade.
crew = [make('Mystery', '', 150), make('Royal_Fortune', 'A', 86)]
check('unknown grade but rested still usable', order(crew), ['Mystery', 'Royal_Fortune'])

# Exactly on the threshold counts as usable.
crew = [
    make('Edge', 'B', COLLECT_MIN_EMOTION),
    make('Tired', 'A', COLLECT_MIN_EMOTION - 1),
]
check('threshold is inclusive', order(crew), ['Edge', 'Tired'])

# Device.click_record_check() trips at 12 clicks on one button, or 6 on each of
# two, so the retry limit has to stay below both.
check('retry limit stays under stuck detection',
      COLLECT_START_CLICK_LIMIT < 6, True)

print(f'{sum(results)}/{len(results)} checks passed')
sys.exit(0 if all(results) else 1)
