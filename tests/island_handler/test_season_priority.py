"""The development season is produced after the menus and the order surplus."""
import pytest

import module.island.season_progress as season_progress
import module.island_handler.season_priority as season_priority
from module.island_handler.restaurant_config import get_config_key
from module.island_handler.season_priority import (
    TIER_IDLE,
    TIER_MENU,
    TIER_SEASON,
    TIER_SURPLUS,
    build_demand_ledger,
    demote_entries,
    get_active_season_id,
    get_free_band,
    get_low_priority_products,
    get_priority_tier,
    get_season_delivery_items,
    get_season_task_items,
    split_by_priority,
)

KOI_MENU_CONFIG = get_config_key(601, 'KoiMenu')
# A dish whose production chain is season-only: 香囊 is milled cloth + lavender.
CLOTH_BAG = 3039
# A material shared by the other menus, it must never be demoted.
MILK = 2603
# Any item the ledger can report as idle-only, its recipe is never looked up.
IRON_NAIL = 3050


SEASON_KEY = 'IslandGeneral.IslandGeneral.Season'
# Seasons are namespaces picked in IslandGeneral, not date ranges.
IN_SEASON = 'season_20261105_cn'
OUT_OF_SEASON = 'season_20260806_cn'
UNKNOWN_SEASON = 'season_19990101_cn'


class FakeConfig:
    def __init__(self, values=None):
        self.values = {SEASON_KEY: IN_SEASON}
        self.values.update(values or {})

    def cross_get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture(autouse=True)
def isolated_progress(monkeypatch, tmp_path):
    """Never read the delivered tasks of the machine the tests run on."""
    monkeypatch.setattr(season_progress, 'PROGRESS_FILE', str(tmp_path / 'progress.json'))


def test_active_season_is_read_from_the_season_data():
    assert get_active_season_id(FakeConfig({SEASON_KEY: UNKNOWN_SEASON})) == 0
    assert get_active_season_id(FakeConfig()) == IN_SEASON


def test_season_task_items_are_empty_without_a_season():
    assert get_season_task_items(FakeConfig({SEASON_KEY: UNKNOWN_SEASON})) == {}


def test_season_task_items_return_the_task_targets():
    targets = get_season_task_items(FakeConfig())
    assert targets
    # Every entry is a positive amount of a known item.
    assert all(amount > 0 for amount in targets.values())


def test_delivery_items_include_the_stuck_season_order():
    config = FakeConfig({'IslandOrder.IslandOrder.StuckSeasonOrderId': 100067})
    delivery = get_season_delivery_items(config)

    assert delivery[MILK] > 0 or delivery
    assert delivery[4051] > 0  # 冬菇炖鸡, requested by the stuck order


def test_season_only_products_are_demoted():
    products = get_low_priority_products(FakeConfig())

    assert CLOTH_BAG in products


def test_menu_products_keep_their_priority():
    config = FakeConfig({KOI_MENU_CONFIG: f'香囊 ({CLOTH_BAG}): 7'})

    assert CLOTH_BAG not in get_low_priority_products(config)


def test_no_season_means_no_demotion():
    assert get_low_priority_products(FakeConfig({SEASON_KEY: UNKNOWN_SEASON})) == frozenset()


def test_demote_entries_moves_the_tail_and_keeps_order():
    # 701010 is 铁钉 (a normal item), 701005 is 香囊 and 701013 刀叉餐具.
    entries = [
        (701005, 'bag', None),
        (701010, 'nail', None),
        (701013, 'tableware', None),
    ]
    products = frozenset({3039, 3047})

    assert demote_entries(entries, products) == [
        (701010, 'nail', None),
        (701005, 'bag', None),
        (701013, 'tableware', None),
    ]


def test_demote_entries_without_products_is_a_no_op():
    entries = [(701010, 'tool', None), (701005, 'bag', None)]

    assert demote_entries(entries, frozenset()) is entries


def test_demand_ledger_splits_the_sub_tasks_by_item():
    ledger = build_demand_ledger(FakeConfig())

    assert ledger[CLOTH_BAG]['season'] > 0
    assert 'menu' not in ledger[CLOTH_BAG]
    # Development funds are spent, never produced, so they are no demand.
    assert 1 not in ledger


def test_tier_is_the_highest_priority_sub_task_asking_for_the_item():
    ledger = {
        MILK: {'menu': 10, 'season': 400},
        CLOTH_BAG: {'season': 10},
        IRON_NAIL: {'idle': 2},
    }

    assert get_priority_tier(MILK, ledger) == TIER_MENU
    assert get_priority_tier(CLOTH_BAG, ledger) == TIER_SEASON
    assert get_priority_tier(IRON_NAIL, ledger) == TIER_IDLE
    # An item nobody asks for is surplus work, it is nobody's priority.
    assert get_priority_tier(999999, ledger) == TIER_SURPLUS


def test_split_keeps_the_weight_order_inside_each_group():
    entries = [(701005, 'bag', None), (701010, 'nail', None), (701013, 'tableware', None)]

    kept, demoted = split_by_priority(entries, frozenset({CLOTH_BAG, 3047}))

    assert kept == [(701010, 'nail', None)]
    assert demoted == [(701005, 'bag', None), (701013, 'tableware', None)]


def test_free_band_is_the_buffer_above_the_menu_reserve(monkeypatch):
    monkeypatch.setattr(season_priority, 'get_menu_reserve_items', lambda config: {CLOTH_BAG: 7})

    band = get_free_band(FakeConfig(), {CLOTH_BAG: 10, MILK: 20})

    # One menu dish reserves a whole shelf tranche, the rest is order fodder.
    assert band[CLOTH_BAG] == 3
    # Nothing reserves milk, orders and transport may take all of it.
    assert band[MILK] == 20


def test_free_band_never_goes_negative(monkeypatch):
    monkeypatch.setattr(season_priority, 'get_menu_reserve_items', lambda config: {CLOTH_BAG: 40})

    band = get_free_band(FakeConfig(), {CLOTH_BAG: 10}, hard_floor_items={CLOTH_BAG: 0})

    assert band[CLOTH_BAG] == 0
