from module.base.button import ButtonGrid
from module.base.timer import Timer
from module.exception import GameStuckError
from module.island.assets import *
from module.island_handler.dock import IslandDock
from module.island_handler.dock_scanner import CharacterScanner
from module.logger import logger
from module.ui.page import page_island_manage, page_island_phone

ISLAND_COLLECT_WORKSLOT_GRID = ButtonGrid(
    origin=(832, 227), delta=(95, 0), button_shape=(74, 74), grid_shape=(3, 1)
)

# Adaptability grade read from the dock card by GradeScanner, best first.
# Unknown grades (OCR miss) go last so a readable card always wins.
GRADE_PRIORITY = {'S': 0, 'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5}
GRADE_UNKNOWN = len(GRADE_PRIORITY)

# The game refuses to start a round when a crew member has less emotion than
# this and the start button simply does nothing, which used to end as a
# GameTooManyClickError and three failed rounds in a row.  Observed:
# 2026-10-23 emotion 100 started fine, 2026-10-25 emotion 86 left the button
# dead, 2026-10-25 emotion 121 started fine.
COLLECT_MIN_EMOTION = 100
# Give up instead of letting Device.click_record_check() declare a game stuck.
# It fires at 12 clicks on one button or 6 on each of two buttons, so stay below
# both: a working round needs two or three clicks.
COLLECT_START_CLICK_LIMIT = 5


def grade_priority(character):
    return GRADE_PRIORITY.get(character.grade, GRADE_UNKNOWN)


def collect_priority(character):
    """Best crew first: enough emotion, then adaptability, then emotion.

    Adaptability decides the output, but a tired character cannot start at all,
    so emotion has to gate the sort before the grade.
    """
    emotion = character.emotion or 0
    return (
        0 if emotion >= COLLECT_MIN_EMOTION else 1,
        grade_priority(character),
        -emotion,
    )


class IslandCollect(IslandDock):
    def collect_location_unselected_buttons(self):
        buttons = []
        if not self.is_button_selected(ISLAND_COLLECT_LOCATION_1):
            buttons.append(ISLAND_COLLECT_LOCATION_1)
        if not self.is_button_selected(ISLAND_COLLECT_LOCATION_2):
            buttons.append(ISLAND_COLLECT_LOCATION_2)
        return buttons

    def collect_available(self):
        for _ in self.loop(timeout=10):
            if self.appear_then_click(ISLAND_COLLECT_SELECT_ENTER, offset=(20, 20), interval=3):
                continue
            # End
            if self.appear(ISLAND_COLLECT_SELECT_CONFIRM, offset=(20, 20)):
                break
        else:
            logger.warning('Cannot find collect enter button, collect may not be available')
            return False

        available = None
        for _ in self.loop(skip_first=False, timeout=20):
            # End
            if self.handle_info_bar():
                logger.warning('Info bar appears, nothing more to collect')
                available = False
                break
            if self.match_template_color(ISLAND_COLLECT_START_UNAVAILABLE, offset=(20, 20)):
                logger.info('has something to collect, continue')
                available = True
                break

            if self.appear(ISLAND_COLLECT_SELECT_CONFIRM, offset=(20, 20)):
                buttons = self.collect_location_unselected_buttons()
                if not buttons:
                    self.device.click(ISLAND_COLLECT_SELECT_CONFIRM)
                    continue
                for button in buttons:
                    self.device.click(button)
                continue
        else:
            logger.warning('Cannot determine collect availability, possibly due to network issues')
            available = False

        if available:
            return True
        # Back to island manage page
        for _ in self.loop(timeout=10):
            if self.appear_then_click(ISLAND_COLLECT_SELECT_CANCEL, offset=(20, 20), interval=3):
                continue
            if self.ui_page_appear(page_island_manage, offset=(20, 20)):
                break
        else:
            logger.warning('Cannot return to island manage page, something may be wrong')
            raise GameStuckError('Cannot return to island manage page, something may be wrong')
        return False

    def collect_execute(self):
        for workslot, button in enumerate(ISLAND_COLLECT_WORKSLOT_GRID.buttons):
            click_timer = Timer(1, count=3)
            for _ in self.loop(timeout=10):
                # End
                if self.is_in_island_dock():
                    break
                if click_timer.reached():
                    self.device.click(button)
                    click_timer.reset()
            else:
                logger.warning(f'Failed to click workslot button for workslot {workslot}')
                return False
            self.island_dock_sort_method_dsc_set(enable=False)
            dock_grid = super().dock_grid
            scanner = CharacterScanner(dock_grid, emotion=(80, 150), status='free')
            scanner.disable('emotion_limit')
            for _ in self.loop(timeout=3):
                candidates = scanner.scan(self.device.image)
                if not candidates:
                    continue
                if any(candidate.identity == 'unknown' for candidate in candidates):
                    continue
                break
            if not candidates:
                logger.warning(f'No candidate found for workslot {workslot}, canceling collect')
                self.island_dock_quit()
                return False
            # Collect output scales with adaptability, so spend the best grade first.
            candidates.sort(key=collect_priority)
            logger.info(
                f'Collect candidates for workslot {workslot}: '
                + ', '.join(
                    f'{candidate.identity}({candidate.grade or "unknown"}, '
                    f'emotion {candidate.emotion})'
                    for candidate in candidates
                )
            )
            if candidates[0].emotion < COLLECT_MIN_EMOTION:
                logger.warning(
                    f'Best candidate {candidates[0].identity} has only '
                    f'{candidates[0].emotion} emotion, the game may refuse to start'
                )
            button = candidates[0].button
            self.island_dock_select_one(button)
            self.island_dock_select_confirm(check_button=[ISLAND_COLLECT_START, ISLAND_COLLECT_START_UNAVAILABLE])
            if self.match_template_color(ISLAND_COLLECT_START, offset=(20, 20)):
                break
        if not self.match_template_color(ISLAND_COLLECT_START, offset=(20, 20)):
            logger.warning('Failed to start collect, something may be wrong')
            return False
        confirm_timer = Timer(3, count=5).start()
        click_count = 0
        for _ in self.loop(timeout=10):
            if self.handle_island_additional():
                confirm_timer.reset()
                continue
            if self.match_template_color(ISLAND_COLLECT_START, offset=(20, 20)):
                # A crew short of emotion leaves the button dead.  Stop before
                # Device.click_record_check() turns it into a game restart and
                # the task ends up requesting a human takeover.
                if click_count >= COLLECT_START_CLICK_LIMIT:
                    logger.warning(
                        'Collect start button is not responding, the selected '
                        'crew is most likely short of emotion, cancel this round'
                    )
                    return False
                self.device.click(ISLAND_COLLECT_START)
                click_count += 1
                confirm_timer.reset()
                continue

            # End
            if self.appear(ISLAND_COLLECT_SELECT_ENTER, offset=(20, 20)):
                if confirm_timer.reached():
                    return True
        else:
            logger.warning('Failed to start collect, something may be wrong')
            return False

    def run(self):
        self.ui_ensure(page_island_manage)
        self.island_manage_side_navbar_ensure(upper=3)

        if self.collect_available():
            success = self.collect_execute()
            if success:
                logger.info('Collect successfully')
                self.config.task_delay(server_update=True)
            else:
                logger.warning('Failed to collect, will retry later')
                self.config.task_delay(success=False)
        else:
            logger.info('Collect not available, possibly due to cooldown')
            for _ in self.loop():
                if self.appear_then_click(ISLAND_COLLECT_SELECT_CANCEL, offset=(20, 20), interval=3):
                    continue
                if self.appear(ISLAND_COLLECT_SELECT_ENTER, offset=(20, 20)):
                    break
            self.config.task_delay(server_update=True)
