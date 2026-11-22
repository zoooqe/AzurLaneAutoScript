"""A task that ends without scheduling itself must not stay overdue for days."""
import types
from datetime import datetime, timedelta

import alas as alas_module

AzurLaneAutoScript = alas_module.AzurLaneAutoScript

MAIN = 'Main'


class FakeConfig:
    def __init__(self, next_run, enabled=True):
        self.data = {MAIN: {'Scheduler': {'NextRun': next_run}}}
        self.enabled = enabled
        self.delays = []

    def is_task_enabled(self, task):
        return self.enabled

    def task_delay(self, minute=None, task=None, **kwargs):
        self.delays.append((task, minute))
        self.data[task or MAIN]['Scheduler']['NextRun'] = datetime.now()


def make_runner(config):
    # `config` is a cached property, an instance attribute shadows it.
    runner = object.__new__(AzurLaneAutoScript)
    runner.config = config
    return runner


def test_preempted_task_is_marked_due_now():
    # Main was stopped by another task becoming due, so it never called
    # task_delay and kept a next run from a week ago.
    stale = datetime.now() - timedelta(days=7)
    config = FakeConfig(stale)

    assert make_runner(config).reschedule_if_idle(MAIN, stale) is True
    assert config.delays == [(MAIN, 0)]


def test_task_that_scheduled_itself_is_left_alone():
    stale = datetime.now() - timedelta(days=7)
    config = FakeConfig(datetime.now() + timedelta(hours=2))

    assert make_runner(config).reschedule_if_idle(MAIN, stale) is False
    assert config.delays == []


def test_disabled_task_is_not_rescheduled():
    stale = datetime.now() - timedelta(days=7)
    config = FakeConfig(stale, enabled=False)

    assert make_runner(config).reschedule_if_idle(MAIN, stale) is False
    assert config.delays == []
