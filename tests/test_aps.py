from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from unittest import TestCase

from openpyxl import load_workbook

from aps.data import Dataset, InputError, Line, Order, build_demand, parse_timestamp, timestamp
from aps.engine import solve
from aps.replan import plan_state, revised_problem
from aps.schedule import Plan, first_problem, report
from aps.validator import violations
from aps.xlsx import audit_xlsx, plan_xlsx


START = parse_timestamp("2026-04-27T08:00")


def demo(inventory=30, qty=100, safety="20.4", calendar=None):
    return Dataset(
        (Order("O1", "客户", "A", "轴承6201-2RS", qty, START, START + 24 * 60, 1),),
        {"A": inventory}, {"A": Decimal(safety)},
        {"N1": Line("N1", frozenset({"6201"}), 60, None, 30, 480)},
        calendar or {}, (),
    )


class DemandTests(TestCase):
    def test_partial_coverage_keeps_safety_requirement(self):
        dataset = demo()
        demand = build_demand(dataset, dataset.orders, START, START + 1440)
        balance = demand.balances[0]
        self.assertEqual(demand.allocated, {"O1": 30})
        self.assertEqual(balance.net, Decimal("90.4"))
        self.assertEqual(demand.jobs["order:O1"].qty, 70)
        self.assertEqual(demand.jobs["stock:A"].qty, 21)
        self.assertEqual(balance.net, balance.order_production + balance.replenishment)

    def test_positive_net_when_order_fully_covered(self):
        data = demo(inventory=108)
        demand = build_demand(data, data.orders, START, START + 1440)
        self.assertEqual(list(demand.jobs), ["stock:A"])
        self.assertEqual(demand.jobs["stock:A"].qty, 13)
        covered_data = demo(inventory=125)
        covered = build_demand(covered_data, covered_data.orders, START, START + 1440)
        self.assertFalse(covered.jobs)

    def test_audit_exports_expected_sheets(self):
        data = demo()
        demand = build_demand(data, data.orders, START, START + 1440)
        workbook = load_workbook(BytesIO(audit_xlsx(data, demand)), read_only=True)
        self.assertIn("数据异常", workbook.sheetnames)
        self.assertEqual(workbook["净需求对账"]["F2"].value, 90.4)
        workbook.close()


class ScheduleTests(TestCase):
    def test_missing_quantity_is_not_an_on_time_order(self):
        data = replace(demo(), lines={"N1": Line("N1", frozenset({"6202"}), 60, None, 30, 480)})
        problem = first_problem(data, START, START + 1440)
        baseline, _, _ = solve(problem, seconds=.04)
        view = report(problem, baseline)
        self.assertEqual(view["metrics"]["on_time"], 0)
        self.assertEqual(view["metrics"]["pending_orders"], 1)
        self.assertEqual(view["metrics"]["missing_order_units"], 70)
        self.assertIn("无适配", view["unassigned"][0]["reason"])

    def test_calendar_accounts_for_processing_and_setup(self):
        data = demo(calendar={"N1": ((START, START + 60),)})
        problem = first_problem(data, START, START + 1440)
        baseline, _, _ = solve(problem, seconds=.04)
        self.assertEqual(baseline.unassigned["order:O1"], 70)
        self.assertEqual(violations(problem, baseline), [])

    def test_validator_catches_overlap_and_conservation(self):
        problem = first_problem(demo(), START, START + 1440)
        baseline, _, _ = solve(problem, seconds=.04)
        checks = violations(problem, Plan("test", (*baseline.batches, baseline.batches[0]), {}))
        self.assertTrue(any("时间重叠" in message for message in checks))
        self.assertTrue(any("无法对账" in message for message in checks))

    def test_completed_inventory_and_interruption_are_preserved_across_replans(self):
        data = demo()
        problem = first_problem(data, START, START + 1440)
        initial, _, _ = solve(problem, seconds=.04)
        request = {"type": "fault", "line": "N1", "duration_minutes": 60, "completed_qty": None}
        state = plan_state(problem, initial)
        with self.assertRaisesRegex(InputError, "实际完成件数"):
            revised_problem(data, state, START + 20, [request])
        request["completed_qty"] = 9
        first = revised_problem(data, state, START + 20, [request])
        base, _, _ = solve(first, seconds=.04)
        self.assertTrue(any(batch.status == "interrupted" and batch.qty == 9 for batch in base.batches))
        self.assertTrue(any(batch.start >= START + 80 for batch in base.batches))
        request["duration_minutes"] = 30
        later = revised_problem(data, plan_state(first, base), START + 50, [request])
        second, _, _ = solve(later, seconds=.04)
        self.assertEqual(violations(later, second), [])
        self.assertEqual(sum(b.qty for b in second.batches if b.job == "order:O1"), 70)

    def test_insert_preserves_existing_stock_and_reserves_new_demand(self):
        data = demo()
        problem = first_problem(data, START, START + 1440)
        initial, _, _ = solve(problem, seconds=.04)
        event = {"type": "insert", "code": "A", "model": "轴承6201-2RS", "customer": "新客户",
                 "qty": 45, "due": timestamp(START + 300), "priority": 1}
        second = revised_problem(data, plan_state(problem, initial), START + 10, [event])
        self.assertEqual(sum(second.demand.allocated.values()), 30)
        plan, _, _ = solve(second, seconds=.04)
        result = report(second, plan)
        self.assertEqual(len(result["orders"]), 2)
        self.assertTrue(any(o["id"] == "I00001" and o["planned"] == 45 for o in result["orders"]))
        export = load_workbook(BytesIO(plan_xlsx(result)), read_only=True)
        self.assertEqual(export.sheetnames, ["作业计划", "订单交付", "换型", "异常"])
        export.close()

    def test_release_invalidates_affected_frozen_work(self):
        data = demo()
        problem = first_problem(data, START, START + 1440)
        initial, _, _ = solve(problem, seconds=.04)
        event = {"type": "delay", "code": "A", "available": timestamp(START + 180)}
        second = revised_problem(data, plan_state(problem, initial), START, [event])
        self.assertFalse(second.pins)
        plan, _, _ = solve(second, seconds=.04)
        self.assertTrue(plan.batches)
        self.assertTrue(all(b.start >= START + 180 for b in plan.batches))

    def test_future_orders_are_excluded_then_added(self):
        data = demo()
        first = data.orders[0]
        newer = replace(first, id="O2", received=START + 240)
        data = replace(data, orders=(first, newer))
        problem = first_problem(data, START, START + 1440)
        self.assertEqual(len(problem.demand.orders), 1)
        plan, _, _ = solve(problem, seconds=.04)
        second = revised_problem(data, plan_state(problem, plan), START + 250,
                                 [{"type": "delay", "code": "A", "available": timestamp(START + 300)}])
        self.assertEqual(len(second.demand.orders), 2)
