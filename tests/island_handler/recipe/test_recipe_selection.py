"""Recipe selection gives up instead of scrolling forever."""
import pytest

import module.island_handler.recipe as recipe_module
from module.island_handler.recipe import IslandRecipe


class InstantTimer:
    """Page turns are rate limited in seconds, the fake loop has no clock."""

    def __init__(self, *args, **kwargs):
        pass

    def reached(self):
        return True

    def reset(self):
        return self


@pytest.fixture(autouse=True)
def instant_page_timer(monkeypatch):
    monkeypatch.setattr(recipe_module, 'Timer', InstantTimer)


class FakeDevice:
    def __init__(self):
        self.clicks = []
        self.cleared = 0

    def click(self, button, *args, **kwargs):
        self.clicks.append(button)

    def click_record_clear(self):
        self.cleared += 1


class FakeGrid:
    def __init__(self, count):
        self.buttons = ['recipe-%d' % index for index in range(count)]


class FakeRecipe(IslandRecipe):
    """Visible recipe ids per page, scrolling only when `pages` has more."""

    def __init__(self, pages, scanned=None, max_iterations=30):
        self.pages = pages
        self.page = 0
        self.scanned = scanned if scanned is not None else [i for page in pages for i in page]
        self.all_recipe_stocks = {recipe_id: 0 for recipe_id in self.scanned}
        self.device = FakeDevice()
        self.selected = None
        self.page_turns = 0
        self.max_iterations = max_iterations

    @property
    def recipe_ids(self):
        return list(self.pages[self.page])

    @property
    def recipe_grid(self):
        return FakeGrid(len(self.pages[self.page]))

    def loop(self, timeout=None, **kwargs):
        for index in range(self.max_iterations):
            yield index

    def get_active_recipe_id(self):
        return self.selected

    def next_recipe_page(self):
        self.page_turns += 1
        if self.page + 1 < len(self.pages):
            self.page += 1

    def prev_recipe_page(self):
        self.page_turns += 1
        if self.page > 0:
            self.page -= 1

    def click_recipe(self, button):
        self.selected = self.pages[self.page][int(button.split('-')[1])]


def make_recipe(pages, scanned=None):
    recipe = FakeRecipe(pages, scanned=scanned)
    recipe.device.click = lambda button, *a, **k: (
        recipe.device.clicks.append(button), recipe.click_recipe(button)
    )
    return recipe


def test_recipe_on_the_first_page_is_selected():
    recipe = make_recipe([[101001, 101002, 101003]])

    assert recipe.set_recipe(101002) is True
    assert recipe.device.clicks == ['recipe-1']
    assert recipe.page_turns == 0


def test_recipe_on_a_later_page_is_selected_after_scrolling():
    recipe = make_recipe([[101001, 101002], [101003, 101007]])

    assert recipe.set_recipe(101007) is True
    assert recipe.device.clicks == ['recipe-1']
    assert recipe.page_turns == 1


def test_unreachable_recipe_stops_instead_of_scrolling_forever():
    # 101007 was in the scan but the page never reaches it: the list swallows
    # the swipe, which is what the log showed before GameTooManyClickError.
    recipe = make_recipe([[101001, 101002, 101003]], scanned=[101001, 101002, 101003, 101007])

    assert recipe.set_recipe(101007) is False
    # It tries both directions, then gives up instead of scrolling to the click
    # limit like the log showed.
    assert 0 < recipe.page_turns <= 8
    assert recipe.device.cleared >= 1


def test_recipe_missing_from_the_scan_is_rejected_without_scrolling():
    recipe = make_recipe([[101001, 101002]], scanned=[101001, 101002])

    assert recipe.set_recipe(101007) is False
    assert recipe.page_turns == 0


def test_wrong_scroll_direction_is_flipped_once():
    # The recipe scan order is stale, so the index comparison sends the list the
    # wrong way. The second silent swipe flips it and reaches the recipe.
    recipe = make_recipe(
        [[101007, 101001], [101002, 101003]],
        scanned=[101002, 101003, 101007, 101001],
    )
    recipe.page = 1

    assert recipe.set_recipe(101007) is True
    assert recipe.page == 0
    assert recipe.device.clicks == ['recipe-0']
