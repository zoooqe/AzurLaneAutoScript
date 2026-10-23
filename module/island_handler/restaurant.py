from datetime import datetime, timedelta

import cv2
import numpy as np
from yaml import safe_load

import module.config.server as server
from module.base.button import ButtonGrid
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.base.utils import color_mask, color_similar
from module.config.utils import get_server_next_update
from module.island.assets import ISLAND_CLICK_SAFE_AREA
from module.island.data import DIC_ISLAND_ITEM, DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE
from module.island.utils import (
    get_task_target_items,
    item_name_with_id,
    load_hard_floor_items,
    load_item_mapping,
    normalize_item_keys,
    production_place_name_with_id,
)
from module.island_handler.assets import *
from module.island_handler.dock import IslandDock
from module.island_handler.restaurant_config import (
    RESTAURANT_IDS,
    WAITRESS_ANY,
    WAITRESS_NONE,
    get_config_key,
    get_menu_reserve_items,
    get_restaurant_capacity,
    get_restaurant_config,
    get_selected_named_waitresses,
    get_waitress_slots,
)
from module.logger import logger
from module.ocr.ocr import Digit
from module.statistics.item import Item, ItemGrid

RESTAURANT_SWIPE_AREA = (583, 208, 1023, 400)
# One restaurant shift lasts 8 hours in game, the same assumption ``IslandBusiness``
# uses when it schedules the next round.
RESTAURANT_SHIFT_LENGTH = timedelta(hours=8)
ISLAND_RESTAURANT_ITEM_ORDER_PRICE = {
    DIC_ISLAND_ITEM[item_id]['name']['en'].replace(' ', '_'): {
        'id': item_id,
        'order_price': DIC_ISLAND_ITEM[item_id]['order_price'],
    }
    for menu in DIC_ISLAND_RESTAURANT_MENU_TO_RECIPE.values()
    for item_id in menu.keys()
}


def restaurant_item_log_text(item, sell_amount=None):
    name = item_name_with_id(item.id) if item.id and item.id in DIC_ISLAND_ITEM else item.name
    detail = f'stock {item.amount}'
    if sell_amount is not None:
        detail += f', sell {sell_amount}'
    if getattr(item, 'tag', None) == 'bonus':
        detail += ', event bonus'
    return f'{name} ({detail})'


class WaitressOccupied(Exception):
    pass


class WaitressUnavailable(Exception):
    """Strict mode: a configured waitress is not on duty, so do not open.

    Raised instead of falling back to a free character, so the restaurant is
    skipped for this round rather than run by someone the user did not pick.
    """

    def __init__(self, message, waitress=None):
        super().__init__(message)
        self.waitress = waitress


class WaitressLocked(Exception):
    """A named waitress is still employed by another restaurant until `finish_time`.

    Unlike WaitressOccupied, the end of her current shift is known, so the
    restaurant can be delayed to that moment instead of replacing her.
    """

    def __init__(self, message, waitress=None, finish_time=None):
        super().__init__(message)
        self.waitress = waitress
        self.finish_time = finish_time


class MenuIncomplete(Exception):
    """Part of the configured menu is not stocked enough to open a full round.

    Raised instead of opening with a partial menu when WaitForFullMenu is on,
    so the restaurant is delayed and retried once production catches up.
    """

    def __init__(self, message, missing=None):
        super().__init__(message)
        self.missing = missing or []


class RestaurantItem(Item):
    IMAGE_SHAPE = (83, 87)

    def predict_valid(self):
        # Mask pixels that are not similar to the background
        mask = color_mask(self.image, (207, 209, 211), threshold=50)
        cv2.bitwise_not(mask, dst=mask)
        sum_ = cv2.countNonZero(mask)
        return sum_ > 400


class RestaurantItemAmount(Digit):
    def pre_process(self, image):
        image = super().pre_process(image)
        image = cv2.copyMakeBorder(image, 4, 2, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        return image


class RestaurantItemGrid(ItemGrid):
    item_class = RestaurantItem
    similarity = 0.75

    def __init__(self, grid: ButtonGrid):
        super().__init__(
            grid,
            templates={},
            template_area=(12, 21, 72, 67),
            amount_area=(42, 67, 77, 85),
            tag_area=(66, 2, 72, 5)
        )
        if server.server == 'jp':
            self.amount_ocr = RestaurantItemAmount([], letter=(220, 220, 220), lang='azur_lane', threshold=128, name='Amount_ocr')
        else:
            self.amount_ocr = Digit([], lang='cnocr', threshold=160, name='Amount_ocr')
        self.load_template_folder('./assets/island/restaurant')

    @staticmethod
    def predict_tag(image):
        color = cv2.mean(np.array(image))[:3]
        if color_similar(color, (79, 201, 108), threshold=30):
            return 'bonus'
        return None

    def predict(self, image, name=True, amount=True, cost=False, price=True, tag=True):
        super().predict(image, name=True, amount=True, cost=False, price=False, tag=True)
        for item in self.items:
            item.id = ISLAND_RESTAURANT_ITEM_ORDER_PRICE.get(item.name, {}).get('id', 0)
            item.price = ISLAND_RESTAURANT_ITEM_ORDER_PRICE.get(item.name, {}).get('order_price', 0)
        items = self.items
        grids = self.grids
        if len(items):
            min_row = grids[0, 0].area[1]
            row = [restaurant_item_log_text(item) for item in items if item.button[1] == min_row]
            logger.info(f'Item row 1: {row}')
            row = [restaurant_item_log_text(item) for item in items if item.button[1] != min_row]
            logger.info(f'Item row 2: {row}')
            return items


class IslandRestaurant(IslandDock):
    working_restaurant_id = None
    # Filled by IslandBusiness before dispatching: {restaurant_id: 'running'|'resting'|'ready'}
    restaurant_states = {}
    restaurant_finish_times = {}

    @cached_property
    def restaurant_capacity(self):
        return {
            restaurant_id: get_restaurant_capacity(self.config, restaurant_id)
            for restaurant_id in RESTAURANT_IDS
        }

    @staticmethod
    def get_quantity_from_grade(grade):
        if grade in ['bronze', 'silver']:
            return 2
        elif grade == 'gold':
            return 3
        elif grade == 'diamond':
            return 4
        else:
            raise ValueError(f"Invalid grade: {grade}")

    @cached_property
    def restaurant_quantity(self):
        quantity = {}
        for restaurant_id in RESTAURANT_IDS:
            config_data = get_restaurant_config(restaurant_id)
            grade = self.config.cross_get(
                get_config_key(restaurant_id, config_data['grade_key'])
            )
            quantity[restaurant_id] = self.get_quantity_from_grade(grade)
        return quantity

    def is_in_island_restaurant(self):
        return self.appear(ISLAND_RESTAURANT_CHECK, offset=(0, 20))

    @cached_property
    def restaurant_has_event(self):
        return self.appear(ISLAND_RESTAURANT_EVENT_CHECK, offset=(20, 20))

    # 364 and 214
    @cached_property
    def _restaurant_offset_x(self):
        if self.restaurant_has_event:
            offset = 150
        else:
            offset = 0
        logger.attr('restaurant_offset_x', offset)
        return offset

    @cached_property
    def _restaurant_offset(self):
        return (self._restaurant_offset_x - 20, -20, self._restaurant_offset_x + 20, 20)

    def receive_revenue(self):
        confirm_timer = Timer(1, count=3)
        for _ in self.loop():
            if self.appear_then_click(ISLAND_RESTAURANT_RECEIVE, offset=self._restaurant_offset, interval=2):
                confirm_timer.reset()
                continue
            if self.appear(ISLAND_RESTAURANT_RESULT, offset=(20, 20), interval=2):
                self.device.click(ISLAND_CLICK_SAFE_AREA)
                confirm_timer.reset()
                continue
            if self.handle_island_additional():
                confirm_timer.reset()
                continue
            # End
            if (self.appear(ISLAND_RESTAURANT_RECOMMEND, offset=self._restaurant_offset)
                    or self.restaurant_resting()):
                if confirm_timer.reached():
                    return True

    @cached_property
    def restaurant_grid(self):
        return ButtonGrid(
            origin=(583 + self._restaurant_offset_x, 208), delta=(89.5, 92),
            button_shape=(83, 87), grid_shape=(5, 2), name='restaurant_grid'
        )

    def swipe_top_to_bottom(self):
        if not self.match_template_color(ISLAND_RESTAURANT_SCROLL_TOP, offset=self._restaurant_offset):
            return False
        box = (RESTAURANT_SWIPE_AREA[0] + self._restaurant_offset_x, RESTAURANT_SWIPE_AREA[1],
               RESTAURANT_SWIPE_AREA[2] + self._restaurant_offset_x, RESTAURANT_SWIPE_AREA[3])
        self.device.swipe_vector((0, -150), box=box, padding=-5)
        for _ in self.loop(timeout=0.8):
            pass
        return True

    def swipe_bottom_to_top(self):
        if not self.match_template_color(ISLAND_RESTAURANT_SCROLL_BOTTOM, offset=self._restaurant_offset):
            return False
        box = (RESTAURANT_SWIPE_AREA[0] + self._restaurant_offset_x, RESTAURANT_SWIPE_AREA[1],
               RESTAURANT_SWIPE_AREA[2] + self._restaurant_offset_x, RESTAURANT_SWIPE_AREA[3])
        self.device.swipe_vector((0, 150), box=box, padding=-5)
        for _ in self.loop(timeout=0.8):
            pass
        return True

    def scan_item_grid(self, grid: ButtonGrid):
        item_grid = RestaurantItemGrid(grid)
        if self.config.SHOP_EXTRACT_TEMPLATE:
            item_grid.extract_template(self.device.image, folder='./assets/island/restaurant')
        item_grid.predict(self.device.image, tag=True)
        return item_grid.items

    def scan_all_items(self):
        items = []
        items_set = set()
        self.swipe_bottom_to_top()
        result = self.scan_item_grid(self.restaurant_grid)
        for item in result:
            if item.name not in items_set:
                items.append(item)
                items_set.add(item.name)
        if self.swipe_top_to_bottom():
            result = self.scan_item_grid(self.restaurant_grid.move((0, 24)))
            for item in result:
                if item.name not in items_set:
                    items.append(item)
                    items_set.add(item.name)
        return items

    @cached_property
    def event_buff(self):
        if not self.restaurant_has_event:
            return 0
        ocr = Digit(ISLAND_RESTAURANT_EVENT_BUFF, lang='cnocr', letter=(67, 71, 23), threshold=160, alphabet='0123IDB')
        result = ocr.ocr(self.device.image)
        if not result in [10, 20, 30]:
            logger.warning(f'Unexpected event buff OCR result: {result}, default to 10')
            result = 10
        return result

    def get_sell_plan(self):
        capacity = self.restaurant_capacity[self.working_restaurant_id]
        menu_key = get_restaurant_config(self.working_restaurant_id)['menu_key']
        menu_text = self.config.cross_get(
            get_config_key(self.working_restaurant_id, menu_key),
            default="{}",
        )
        menu = normalize_item_keys(safe_load(menu_text))
        protected_items = self.restaurant_protected_items
        def total_revenue_estimate(item):
            amount = min(item.amount, capacity)
            if item.tag == 'bonus':
                return item.price * amount * (1 + self.event_buff / 100)
            return item.price * amount
        def has_sellable_capacity(item):
            return item.amount >= capacity + protected_items.get(item.id, 0)
        items = self.scan_all_items()
        menu_items = [
            item for item in items
            if item.id in menu
            # Sell one full waitress-capacity tranche while preserving manual
            # hard floors, task targets, and remaining season-order
            # requirements. Configured menu sales may consume their soft
            # restaurant reserve and daily buffer.
            and has_sellable_capacity(item)
        ]
        quantity = self.restaurant_quantity[self.working_restaurant_id]
        if self.wait_for_full_menu and menu:
            ready_ids = {item.id for item in menu_items}
            # Only the dishes that can actually take a shelf are required. A menu
            # longer than the shelf count is a list of choices, not a promise to
            # sell them all at once, so a dish beyond the shelf count must not
            # hold the round back.
            required = min(len(menu), quantity)
            if len(ready_ids) < required:
                # Opening with a short menu would silently sell unplanned items,
                # so delay the round until production catches up.
                stock_map = {item.id: item.amount for item in items}
                missing = sorted(item_id for item_id in menu if item_id not in ready_ids)
                restaurant = production_place_name_with_id(self.working_restaurant_id)
                detail = ', '.join(
                    f'{item_name_with_id(item_id)} '
                    f'{stock_map.get(item_id, 0)}/'
                    f'{capacity + protected_items.get(item_id, 0)}'
                    for item_id in missing
                )
                raise MenuIncomplete(
                    f'Restaurant {restaurant} waits for its full menu, '
                    f'not enough: {detail}',
                    missing=missing,
                )
        plan = sorted(menu_items, key=total_revenue_estimate, reverse=True)[:quantity]
        remaining = quantity - len(plan)
        if remaining:
            # Fill only vacant shelves from stock above the complete operating floor.
            daily_buffer_items = normalize_item_keys(load_item_mapping(
                self.config.cross_get("IslandProduction.IslandProduction.DailyBufferItems", "{}"),
                config_name='DailyBufferItems',
            ))
            manual_buffer_items = normalize_item_keys(load_item_mapping(
                self.config.cross_get("IslandProduction.IslandProduction.ManualBufferItems", "{}"),
                config_name='ManualBufferItems',
            ))
            menu_reserve_items = get_menu_reserve_items(self.config)
            def has_surplus_capacity(item):
                operation_floor = (
                    protected_items.get(item.id, 0)
                    + menu_reserve_items.get(item.id, 0)
                    + max(daily_buffer_items.get(item.id, 0), manual_buffer_items.get(item.id, 0), 0)
                )
                return item.amount >= capacity + operation_floor
            surplus_items = [
                item for item in items
                if item.id not in menu and has_surplus_capacity(item)
            ]
            plan.extend(sorted(surplus_items, key=total_revenue_estimate, reverse=True)[:remaining])
        restaurant = production_place_name_with_id(self.working_restaurant_id)
        menu_summary = ', '.join(
            f'{item_name_with_id(item_id)} x{amount}'
            for item_id, amount in sorted(menu.items())
        ) or '-'
        plan_summary = ', '.join(
            restaurant_item_log_text(item, sell_amount=capacity) for item in plan
        ) or '-'
        logger.info(f'Restaurant configured menu for {restaurant}: {menu_summary}')
        if len(menu) > quantity:
            logger.info(
                f'Restaurant {restaurant} menu lists {len(menu)} dishes but the grade only '
                f'unlocks {quantity} shelves, the extra dishes are not sold this round'
            )
        logger.info(f'Restaurant sell plan for {restaurant}: {plan_summary}')
        return plan

    @cached_property
    def restaurant_protected_items(self):
        hard_floor_items = normalize_item_keys(load_hard_floor_items(
            self.config.cross_get("IslandProduction.IslandProduction.HardFloorItems", "")
        ))
        protected_target_items = get_task_target_items(self.config)
        item_ids = set(hard_floor_items) | set(protected_target_items)
        return {
            item_id: max(hard_floor_items.get(item_id, 0), 0)
            + max(protected_target_items.get(item_id, {}).get('total_need_count', 0), 0)
            for item_id in item_ids
        }

    @cached_property
    def waitress_lists(self):
        return {
            restaurant_id: list(get_waitress_slots(self.config, restaurant_id))
            for restaurant_id in RESTAURANT_IDS
        }

    def unavailable_waitress_list(self):
        current_waitresses = get_selected_named_waitresses(
            get_waitress_slots(self.config, self.working_restaurant_id)
        )
        lst = set()
        for restaurant_id in RESTAURANT_IDS:
            if restaurant_id == self.working_restaurant_id:
                continue
            lst.update(
                get_selected_named_waitresses(
                    get_waitress_slots(self.config, restaurant_id)
                )
            )
        return sorted(lst - current_waitresses)

    @property
    def wait_for_full_menu(self):
        """Open a restaurant only when every configured dish is stocked enough.

        When off, a partially stocked menu is topped up with other sellable
        items so the round still runs, which was the old behaviour.
        """
        return bool(self.config.cross_get(
            "IslandBusiness.IslandRestaurant.WaitForFullMenu", True
        ))

    @property
    def waitress_lock_enabled(self):
        return bool(self.config.cross_get(
            "IslandBusiness.IslandRestaurant.WaitressLock", True
        ))

    @property
    def strict_waitress(self):
        """Open only when every configured waitress is actually on duty.

        Off by default: an unavailable waitress is quietly replaced by a free
        character so the round still runs. Turning this on skips the round
        instead, which never puts someone the user did not pick on a slot.
        """
        return bool(self.config.cross_get(
            "IslandBusiness.IslandRestaurant.StrictWaitress", False
        ))

    def locked_waitress_status(self, waitress):
        """Where ``waitress`` is currently working, and whether she is idle.

        A named waitress can be configured for several restaurants at once.  She
        is only really "unavailable" while one of those restaurants is running,
        so a restaurant that is resting (or has not opened yet today) must not
        make her look occupied.

        Returns:
            (status, restaurant_id, finish_time)
            status: 'running'  she is employed by ``restaurant_id``,
                    'idle'     no configured restaurant is running, she is free,
                    'unknown'  the state of a configured restaurant is unknown.
        """
        states = getattr(self, 'restaurant_states', None) or {}
        finish_times = getattr(self, 'restaurant_finish_times', None) or {}
        idle = True
        for restaurant_id in RESTAURANT_IDS:
            if restaurant_id == self.working_restaurant_id:
                continue
            if waitress not in get_selected_named_waitresses(
                    get_waitress_slots(self.config, restaurant_id)):
                continue
            state = states.get(restaurant_id)
            if state == 'running':
                return 'running', restaurant_id, finish_times.get(restaurant_id)
            if state not in ('resting', 'ready'):
                idle = False
        if idle:
            return 'idle', None, None
        return 'unknown', None, None

    @staticmethod
    def should_lock_waitress(finish_time):
        """Decide whether this restaurant should wait for her current shift.

        Waiting only pays off while both restaurants still want a shift today
        and another full shift still fits before the daily reset.  A shift that
        already runs past 00:00 is always waited out: opening the restaurant
        right away would only burn a late-night shift, and she would be free
        again in the new day anyway.
        """
        next_update = get_server_next_update("00:00")
        if finish_time >= next_update:
            return True
        return finish_time + RESTAURANT_SHIFT_LENGTH <= next_update

    def restaurant_running(self):
        return self.appear(ISLAND_RESTAURANT_RUNNING, offset=self._restaurant_offset)

    def restaurant_resting(self):
        return self.appear(ISLAND_RESTAURANT_RESTING, offset=self._restaurant_offset)

    def choose_waitress(self):
        waitress_list = list(get_waitress_slots(self.config, self.working_restaurant_id))
        active_waitresses = [
            waitress for waitress in waitress_list
            if waitress != WAITRESS_NONE
        ]
        if not active_waitresses:
            return False
        unavailable_waitress_list = set(self.unavailable_waitress_list())
        named_waitresses = [
            waitress for waitress in active_waitresses
            if waitress not in (WAITRESS_ANY, WAITRESS_NONE)
        ]
        all_named_waitresses = set(named_waitresses)
        selected_waitresses = set()
        if unavailable_waitress_list:
            logger.warning(f"Unavailable waitress list: {sorted(unavailable_waitress_list)}")
        for _ in self.loop():
            if self.appear_then_click(ISLAND_RESTAURANT_SELECT_CHARACTER, offset=self._restaurant_offset, interval=2):
                continue
            if self.is_in_island_dock():
                break
        success = True
        restaurant = production_place_name_with_id(self.working_restaurant_id)
        for waitress in named_waitresses:
            if waitress in unavailable_waitress_list:
                status, employer_id, locked_until = self.locked_waitress_status(waitress)
                if status == 'idle':
                    # No configured restaurant is running her, so she is not
                    # occupied at all; pick her normally instead of replacing her.
                    logger.info(
                        f"Waitress {waitress} is not employed by any running restaurant, "
                        f"treat her as free for restaurant {restaurant}"
                    )
                    unavailable_waitress_list.remove(waitress)
                elif not self.waitress_lock_enabled:
                    logger.warning(
                        f"Waitress {waitress} is already assigned to another restaurant, "
                        f"skip it for restaurant {restaurant}"
                    )
                    unavailable_waitress_list.remove(waitress)
                    continue
                elif status == 'running' and locked_until is not None:
                    employer = production_place_name_with_id(employer_id)
                    if self.should_lock_waitress(locked_until):
                        logger.info(
                            f"Waitress {waitress} is locked by {employer} until "
                            f"{locked_until}, delay restaurant {restaurant} for her "
                            f"instead of replacing her"
                        )
                        self.ui_back(check_button=self.is_in_island_restaurant)
                        raise WaitressLocked(
                            f"Restaurant {restaurant} waits for waitress {waitress} "
                            f"until {locked_until}",
                            waitress=waitress,
                            finish_time=locked_until,
                        )
                    logger.warning(
                        f"Waitress {waitress} is employed by {employer} until "
                        f"{locked_until}, too late to fit another shift before "
                        f"00:00, replace her for restaurant {restaurant}"
                    )
                    unavailable_waitress_list.remove(waitress)
                    continue
                else:
                    logger.warning(
                        f"Waitress {waitress} is already assigned to another restaurant, "
                        f"skip it for restaurant {restaurant}"
                    )
                    unavailable_waitress_list.remove(waitress)
                    continue
            candidate = self.island_dock_find_character(waitress)
            if candidate is not None and candidate.status == 'free':
                self.island_dock_select_one(candidate.button)
                selected_waitresses.add(candidate.identity)
                continue

            if self.strict_waitress:
                state = candidate.status if candidate is not None else 'not found in the dock'
                # She may simply be on shift at another configured restaurant.
                # Her end time is known there, so queue this restaurant right
                # after she finishes instead of polling every hour.
                status, employer_id, locked_until = self.locked_waitress_status(waitress)
                if status == 'running' and locked_until and locked_until > datetime.now():
                    employer = production_place_name_with_id(employer_id)
                    logger.info(
                        f"Waitress {waitress} is employed by {employer} until "
                        f"{locked_until}, strict waitress is on: start restaurant "
                        f"{restaurant} right after she is free"
                    )
                    self.ui_back(check_button=self.is_in_island_restaurant)
                    raise WaitressLocked(
                        f"Restaurant {restaurant} waits for waitress {waitress} "
                        f"until {locked_until}",
                        waitress=waitress,
                        finish_time=locked_until,
                    )
                logger.warning(
                    f"Waitress {waitress} is {state}, strict waitress is on: "
                    f"skip restaurant {restaurant} this round instead of "
                    f"picking a substitute"
                )
                self.ui_back(check_button=self.is_in_island_restaurant)
                raise WaitressUnavailable(
                    f"Restaurant {restaurant} skipped, waitress {waitress} is {state}",
                    waitress=waitress,
                )

            if candidate is not None:
                time_until_update = get_server_next_update("00:00") - datetime.now()
                if time_until_update >= timedelta(hours=8):
                    logger.warning(
                        f"Waitress {waitress} not available, delaying restaurant "
                        f"{restaurant} for 8 hours"
                    )
                    self.ui_back(check_button=self.is_in_island_restaurant)
                    raise WaitressOccupied(
                        f"Waitress {waitress} is occupied, delaying restaurant "
                        f"{restaurant} for 8 hours"
                    )

            self.ensure_dock_page_at_top()
            fallback_blacklist = (
                unavailable_waitress_list
                | selected_waitresses
                | all_named_waitresses
            )
            candidate = self.island_dock_find_character_with_blacklist(fallback_blacklist)
            if candidate is None:
                success = False
            else:
                self.island_dock_select_one(candidate.button)
                selected_waitresses.add(candidate.identity)

        for _ in range(active_waitresses.count(WAITRESS_ANY)):
            blacklist = unavailable_waitress_list | selected_waitresses | all_named_waitresses
            candidate = self.island_dock_find_character_with_blacklist(blacklist)
            if candidate is None:
                success = False
            else:
                self.island_dock_select_one(candidate.button)
                selected_waitresses.add(candidate.identity)
        if not success:
            logger.warning("Failed to choose waitress")
            self.ui_back(check_button=self.is_in_island_restaurant)
        else:
            self.island_dock_select_confirm(check_button=self.is_in_island_restaurant, skip_first=False)
        return success

    def select_dishes(self):
        plan = self.get_sell_plan()
        if not plan:
            return False
        self.swipe_bottom_to_top()
        result = self.scan_item_grid(self.restaurant_grid)
        plan_to_click = []
        for item in result:
            if item in plan:
                plan_to_click.append(item)
        for _ in self.loop():
            for item in plan_to_click:
                if self.is_button_selected(item._button):
                    plan.remove(item)
                    plan_to_click.remove(item)
                else:
                    self.device.click(item._button)
            if not plan_to_click:
                break
        if not plan:
            return True
        if self.swipe_top_to_bottom():
            result = self.scan_item_grid(self.restaurant_grid.move((0, 24)))
            plan_to_click = []
            for item in result:
                if item in plan:
                    plan_to_click.append(item)
            for _ in self.loop():
                for item in plan_to_click:
                    if self.is_button_selected(item._button):
                        plan.remove(item)
                        plan_to_click.remove(item)
                    else:
                        self.device.click(item._button)
                if not plan_to_click:
                    break
        if plan:
            restaurant = production_place_name_with_id(self.working_restaurant_id)
            remaining = [restaurant_item_log_text(item) for item in plan]
            logger.warning(f'Failed to select dishes for {restaurant}; remaining: {remaining}')
        return not plan

    def restaurant_start(self):
        for _ in self.loop():
            if self.handle_island_additional():
                continue
            if self.appear_then_click(ISLAND_RESTAURANT_START, offset=self._restaurant_offset, interval=2):
                continue
            if self.restaurant_running():
                return True

    def run(self):
        if self.restaurant_running():
            logger.info("Restaurant is already running, skip this round")
            return False
        self.receive_revenue()
        if self.restaurant_resting():
            logger.info("Restaurant is resting, finish this round")
            return False
        if not self.choose_waitress():
            logger.warning("Failed to choose waitress, skip this round")
            return False
        if not self.select_dishes():
            logger.warning("Failed to select all dishes, skip this round")
            return False
        if not self.restaurant_start():
            logger.warning("Failed to start restaurant, skip this round")
            return False
        return True
