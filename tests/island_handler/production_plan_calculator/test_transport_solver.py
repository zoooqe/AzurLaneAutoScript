"""Cover the problem-building API added for the transport commission solver.

Upstream rewrote the island tests around screenshot goldens and split the old
``test_production_plan_calculator.py`` into one module per topic.  These cases
exercise ``build_production_problem`` / ``extend_problem`` and the planner
helpers ``create_production_problem`` / ``load_planner_demands``, which exist
so the commission MILP can reuse the island production problem.
"""
from datetime import datetime

import numpy as np
import pytest

import module.config.server as server
from module.island.data import DIC_ISLAND_ACTIVITY, DIC_ISLAND_ITEM
from module.island.utils import get_current_season_remaining_days
from module.island_handler.milp import LinearConstraint, milp
from module.island_handler.production_plan_calculator import (
    ProductionPlanCalculator,
    extend_problem,
)
from module.island_handler.production_planner import (
    IslandProductionPlanner,
    create_production_calculator,
    create_production_problem,
    load_planner_demands,
)

EPS = 1e-4


class FakePlannerConfig:
    def __init__(self, values):
        self.values = values

    def cross_get(self, key, default=None):
        return self.values.get(key, default)


def build_calculator(technology_status=None, **kwargs):
    if technology_status is None:
        technology_status = {}
    return ProductionPlanCalculator(technology_status, **kwargs)


class TestPlannerConfig:
    @staticmethod
    def season_midpoint():
        activity = DIC_ISLAND_ACTIVITY[990023]
        start = datetime.strptime(activity['start_time'][server.server], '%Y-%m-%d %H:%M:%S')
        end = datetime.strptime(activity['end_time'][server.server], '%Y-%m-%d %H:%M:%S')
        return start + (end - start) / 2

    @staticmethod
    def planner_config(**values):
        config = {
            'IslandProductionPlanner.IslandProductionPlanner.FieldsEfficiency': 0,
            'IslandProductionPlanner.IslandProductionPlanner.OrchardEfficiency': 0,
            'IslandProductionPlanner.IslandProductionPlanner.NurseryEfficiency': 0,
            'IslandProductionPlanner.Storage.Storage.IslandTechnologyStatus': {},
            'IslandSeasonTask.IslandSeasonTask.TaskTarget': '{2700: 100}',
            'IslandOrder.IslandOrder.StuckSeasonOrderId': 100015,
        }
        config.update(values)
        return FakePlannerConfig(config)

    def test_load_planner_demands(self):
        current_time = self.season_midpoint()
        config = self.planner_config()
        assert load_planner_demands(config, current_time) == {
            'task_target_items': {2700: 100},
            'task_target_period': get_current_season_remaining_days(current_time),
            'stuck_season_order_id': 100015,
            'current_time': current_time,
        }
        # Given values win over config
        demands = load_planner_demands(config, current_time, task_target_items={}, stuck_season_order_id=0)
        assert demands['task_target_items'] == {}
        assert demands['stuck_season_order_id'] == 0

    def test_create_production_problem_matches_planner_run(self):
        current_time = self.season_midpoint()
        config = self.planner_config()
        planner = object.__new__(IslandProductionPlanner)
        planner.config = config
        solved = planner.run(hard_floor_items_yaml={}, export=False, current_time=current_time)

        problem = create_production_problem(config, current_time)
        assert problem['demand_items'] == solved.demand_items
        assert problem['stuck_season_order_id'] == solved.stuck_season_order_id == 100060
        result = milp(problem['c'], constraints=problem['constraints'], bounds=problem['bounds'])
        rebuilt = create_production_calculator(config, {}, current_time)
        rebuilt._apply_production_lp_result(result, problem)
        assert rebuilt.lp_success
        assert rebuilt.production_plan == pytest.approx(solved.production_plan)
        assert rebuilt.net_items == pytest.approx(solved.net_items)

    def test_create_production_problem_needs_saved_technology(self):
        config = self.planner_config(**{'IslandProductionPlanner.Storage.Storage.IslandTechnologyStatus': None})
        assert create_production_problem(config, self.season_midpoint()) is None


class TestProblemBuilding:
    STONE_BALANCE = ('item_balance', 2700)

    def test_build_does_not_solve_and_matches_solve(self):
        kwargs = {'task_target_items': {2700: 100}, 'task_target_period': 20}
        solved = build_calculator({})
        solved.solve_production_plan(**kwargs)
        builder = build_calculator({})
        problem = builder.build_production_problem(**kwargs)
        assert builder.lp_status is None
        assert builder.production_plan == {}
        row = problem['constraint_labels'].index(('demand', 2700))
        assert problem['constraints'].lb[row] == pytest.approx(5.0)

        result = milp(problem['c'], constraints=problem['constraints'], bounds=problem['bounds'])
        builder._apply_production_lp_result(result, problem)
        assert builder.lp_success
        assert builder.production_plan == pytest.approx(solved.production_plan)
        assert builder.net_items == pytest.approx(solved.net_items)
        assert builder.demand_items == solved.demand_items

    def test_extend_appends_without_touching_original(self):
        problem = build_calculator({}).build_production_problem()
        n = len(problem['c'])
        m = problem['constraints'].A.shape[0]
        original_a = problem['constraints'].A.copy()
        original_labels = list(problem['constraint_labels'])

        extended = extend_problem(problem, columns=[
            {'label': 'extra_stone', 'cost': -1, 'ub': 10, 'rows': {self.STONE_BALANCE: 1}},
            {'label': 'allow', 'ub': 1, 'integer': True},
        ], rows=[
            {'label': 'gate', 'columns': {'extra_stone': 1, 'allow': -10}, 'ub': 0},
        ])
        assert extended['constraints'].A.shape == (m + 1, n + 2)
        assert extended['constraint_labels'] == original_labels + ['gate']
        assert extended['column_labels'] == {'extra_stone': n, 'allow': n + 1}
        assert extended['constraints'].A[original_labels.index(self.STONE_BALANCE), n] == 1
        assert list(extended['constraints'].A[m, n:]) == [1, -10]
        assert list(extended['c'][n:]) == [-1, 0]
        assert list(extended['integrality']) == [0] * n + [0, 1]
        assert list(extended['bounds'].ub[n:]) == [10, 1]
        np.testing.assert_array_equal(extended['constraints'].A[:m, :n], original_a)
        # The input problem is not modified
        assert problem['constraints'].A.shape == (m, n)
        np.testing.assert_array_equal(problem['constraints'].A, original_a)
        assert problem['constraint_labels'] == original_labels
        assert 'column_labels' not in problem and 'integrality' not in problem

        twice = extend_problem(extended, columns=[{'label': 'more', 'integer': True}],
                               rows=[{'label': 'cap', 'columns': {'more': 1, n: 1}, 'ub': 5}])
        assert twice['column_labels'] == {'extra_stone': n, 'allow': n + 1, 'more': n + 2}
        assert list(twice['integrality'][n:]) == [0, 1, 1]
        assert twice['constraints'].A[m + 1, n] == 1

    def test_column_on_balance_row_acts_as_supply(self):
        problem = build_calculator({}).build_production_problem()
        extended = extend_problem(problem, columns=[
            {'label': 'extra_stone', 'ub': 10, 'rows': {self.STONE_BALANCE: 1}},
            {'label': 'allow', 'cost': 1e-3, 'ub': 1, 'integer': True},
        ], rows=[
            {'label': 'gate', 'columns': {'extra_stone': 1, 'allow': -10}, 'ub': 0},
        ])
        result = milp(extended['c'], integrality=extended['integrality'],
                      constraints=extended['constraints'], bounds=extended['bounds'])
        assert result.success
        stone = result.x[extended['column_labels']['extra_stone']]
        allow = result.x[extended['column_labels']['allow']]
        # Extra stone is worth at least its pt_num, more than the gate cost
        assert allow == pytest.approx(1)
        assert stone == pytest.approx(10)

        # Same optimum as raising the stone supply of the original problem by 10
        row = problem['constraint_labels'].index(self.STONE_BALANCE)
        lb = problem['constraints'].lb.copy()
        ub = problem['constraints'].ub.copy()
        lb[row] -= stone
        ub[row] -= stone
        shifted = milp(problem['c'], constraints=LinearConstraint(problem['constraints'].A, lb, ub),
                       bounds=problem['bounds'])
        assert shifted.fun == pytest.approx(result.fun - 1e-3 * allow, abs=1e-6)
        base = milp(problem['c'], constraints=problem['constraints'], bounds=problem['bounds'])
        assert base.fun - shifted.fun >= 10 * DIC_ISLAND_ITEM[2700]['pt_num'] - EPS

    def test_extend_rejects_bad_labels(self):
        problem = build_calculator({}).build_production_problem()
        with pytest.raises(ValueError):
            extend_problem(problem, rows=[{'label': self.STONE_BALANCE, 'columns': {}}])
        with pytest.raises(ValueError):
            extend_problem(problem, columns=[{'label': 3}])
        with pytest.raises(ValueError):
            extend_problem(problem, columns=[{'label': 'a'}, {'label': 'a'}])
        with pytest.raises(KeyError):
            extend_problem(problem, columns=[{'label': 'a', 'rows': {('item_balance', -1): 1}}])
