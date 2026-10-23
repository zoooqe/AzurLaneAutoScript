# -*- coding: utf-8 -*-
"""Offline checks for ui_get_current_page() escaping an unknown island sub page.

2026-10-25 21:10 the game was left in the production detail of 木料加工设备
because the previous run was stopped mid-flow. That screen has no HOME button,
so the next start died with "Please switch to a supported page before starting
Alas" instead of clicking the back arrow and walking out.

Run with the bundled python:
    toolkit/python.exe dev_tools/island_page_recover_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from module.exception import GamePageUnknownError
from module.ui.page import page_island_manage, page_main
from module.ui.ui import UNKNOWN_PAGE_BACK_LIMIT, UI
from module.ui_white.assets import BACK_ARROW_WHITE

results = []


def check(name, actual, expect):
    ok = actual == expect
    results.append(ok)
    print('[{}] {} -> {} (expect {})'.format('PASS' if ok else 'FAIL', name, actual, expect))


class Config:
    Emulator_ControlMethod = 'minitouch'
    Emulator_ScreenshotMethod = 'nemu_ipc'
    SERVER = 'cn'


class Device:
    has_cached_image = True

    def screenshot(self):
        pass

    def app_is_running(self):
        return True

    def get_orientation(self):
        return 1


class Stub:
    """Minimal UI that only feeds the page detection loop.

    depth is how many back presses are needed before a known page shows up.
    """

    def __init__(self, page=None, depth=0, arrow=True):
        self.config = Config()
        self.device = Device()
        self.page = page
        self.depth = depth
        self.arrow = arrow
        self.back_clicks = 0
        self.other_clicks = 0

    def ui_page_appear(self, page, offset=(30, 30), interval=0):
        return self.page is not None and page is self.page

    def appear_then_click(self, button, offset=0, interval=0):
        if button is BACK_ARROW_WHITE:
            if self.arrow and self.depth > 0:
                self.depth -= 1
                self.back_clicks += 1
                if self.depth == 0:
                    self.page = page_island_manage
                return True
            return False
        self.other_clicks += 1
        return False

    def ui_additional(self, get_ship=True):
        return False


def recover(stub):
    """Run the real loop, return the page it settled on or None when it gave up."""
    try:
        return UI.ui_get_current_page(stub, skip_first_screenshot=True)
    except GamePageUnknownError:
        return None


# The reported page: production detail left open, one press of back returns to
# island manage, which is a known page.
stub = Stub(page=None, depth=1)
check('island sub page -> island manage', recover(stub), page_island_manage)
check('island sub page -> one back press', stub.back_clicks, 1)

# Nested deeper, e.g. character select inside production detail.
stub = Stub(page=None, depth=3)
check('nested sub pages -> island manage', recover(stub), page_island_manage)
check('nested sub pages -> three back presses', stub.back_clicks, 3)

# Back arrow present but does nothing, must stop instead of looping forever.
stub = Stub(page=None, depth=99)
check('dead arrow -> gives up', recover(stub), None)
check('dead arrow -> bounded clicks', stub.back_clicks, UNKNOWN_PAGE_BACK_LIMIT)

# No arrow at all, keep the old behaviour and never click blindly.
stub = Stub(page=None, depth=0, arrow=False)
check('no arrow -> gives up', recover(stub), None)
check('no arrow -> no click', stub.back_clicks, 0)

# Already on a known page, nothing to do.
stub = Stub(page=page_main)
check('known page -> returned as is', recover(stub), page_main)
check('known page -> no click', stub.back_clicks, 0)

print('=' * 60)
print('{} checks passed, {} failed'.format(results.count(True), results.count(False)))
sys.exit(0 if all(results) else 1)
