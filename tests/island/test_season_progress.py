"""Delivered development season tasks stop being a demand."""
import os

import module.island.season_progress as season_progress
from module.island_handler.season_priority import expand_items, get_season_task_items
from module.island.season_progress import (
    clear_completed,
    get_completed_task_ids,
    mark_completed,
)

# Seasons are namespaces now, the one in use is picked in IslandGeneral.
SEASON = 'season_20261105_cn'
OTHER_SEASON = 'season_20260806_cn'

# 柑橘补给 asks for 500 citrus, 银矿采集 for 25 silver ore.
CITRUS_TASK = 80001502
SILVER_TASK = 80001510
CITRUS = 2017
SILVER = 2705


class FakeConfig:
    """Only the season namespace is read from the config."""

    def __init__(self, season=SEASON):
        self.season = season

    def cross_get(self, key, default=None):
        if key == 'IslandGeneral.IslandGeneral.Season':
            return self.season
        return default


CONFIG = FakeConfig()


def test_nothing_is_delivered_without_a_record(tmp_path):
    path = str(tmp_path / 'progress.json')

    assert get_completed_task_ids(CONFIG, path) == set()


def test_marking_a_task_records_it_for_the_season(tmp_path):
    path = str(tmp_path / 'progress.json')

    added = mark_completed({CITRUS_TASK}, CONFIG, path)

    assert added == {CITRUS_TASK}
    assert get_completed_task_ids(CONFIG, path) == {CITRUS_TASK}
    # Marking it twice adds nothing, the date of the first delivery is kept.
    assert mark_completed({CITRUS_TASK}, CONFIG, path) == set()


def test_a_record_of_another_season_is_ignored(tmp_path):
    path = str(tmp_path / 'progress.json')
    mark_completed({CITRUS_TASK}, CONFIG, path)

    # Next season nobody has delivered anything yet.
    assert get_completed_task_ids(FakeConfig(OTHER_SEASON), path) == set()


def test_clear_forgets_the_delivered_tasks(tmp_path):
    path = str(tmp_path / 'progress.json')
    mark_completed({CITRUS_TASK, SILVER_TASK}, CONFIG, path)

    assert clear_completed({CITRUS_TASK}, CONFIG, path) == {CITRUS_TASK}
    assert get_completed_task_ids(CONFIG, path) == {SILVER_TASK}

    assert clear_completed(None, CONFIG, path) == {SILVER_TASK}
    assert get_completed_task_ids(CONFIG, path) == set()


def test_broken_record_is_ignored(tmp_path):
    path = tmp_path / 'progress.json'
    path.write_text('not json', encoding='utf-8')

    assert get_completed_task_ids(CONFIG, str(path)) == set()


def test_a_record_keyed_by_the_plain_season_number_is_still_read(tmp_path):
    path = tmp_path / 'progress.json'
    # Records written before the season namespaces were split stored `5`.
    season_progress.save_progress(
        {'season': 5, 'completed': [{'id': CITRUS_TASK, 'name': '柑橘补给', 'date': '2026-11-08 12:00:00'}]},
        str(path),
    )

    assert get_completed_task_ids(CONFIG, str(path)) == {CITRUS_TASK}


def test_delivered_tasks_are_not_asked_for_any_more(monkeypatch, tmp_path):
    monkeypatch.setattr(season_progress, 'PROGRESS_FILE', str(tmp_path / 'progress.json'))
    before = get_season_task_items(CONFIG)
    assert before[CITRUS] > 0 and before[SILVER] > 0

    mark_completed({CITRUS_TASK}, CONFIG)

    after = get_season_task_items(CONFIG)
    # The citrus task is gone, the silver task still asks for its ore.
    assert CITRUS not in after
    assert after[SILVER] == before[SILVER]


def test_silver_ore_drops_only_by_the_amount_of_the_delivered_task(monkeypatch, tmp_path):
    monkeypatch.setattr(season_progress, 'PROGRESS_FILE', str(tmp_path / 'progress.json'))
    before = expand_items(get_season_task_items(CONFIG))[SILVER]

    mark_completed({SILVER_TASK}, CONFIG)

    after = expand_items(get_season_task_items(CONFIG))[SILVER]
    # 钟表 and 刀叉餐具 still consume one ore each, so only the 25 of the mining
    # task itself is gone, not the whole ore band.
    assert before - after == 25
    assert after == 20


def test_marking_without_a_known_season_does_nothing(tmp_path):
    path = str(tmp_path / 'progress.json')

    # An unknown namespace has no task list, so nothing can be recorded.
    unknown = 'season_19990101_cn'
    assert mark_completed({CITRUS_TASK}, CONFIG, path, season_name=unknown) == set()
    assert get_completed_task_ids(CONFIG, path, season_name=unknown) == set()


def test_config_without_the_option_falls_back_to_the_newest_season():
    # Configs written before the season namespaces were split have no option,
    # production has to keep working instead of dropping every season task.
    season_id, season = season_progress.get_active_season(FakeConfig(None))
    assert season_id and season
    assert season['task_list']


def test_progress_file_is_not_an_alas_instance():
    # alas_instance() lists every config/*.json as an Alas instance, so a state
    # file in the config root shows up as a phantom button in the GUI sidebar.
    folder = os.path.dirname(season_progress.PROGRESS_FILE).replace('\\', '/')
    assert folder not in ('', 'config', './config')
