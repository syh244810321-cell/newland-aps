from datetime import datetime
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import update

from aps.product.api import make_app
from aps.product.service import Service
from aps.product.tables import jobs


def workbook(quantity=100):
    book = Workbook()
    order = book.active
    order.title = "订单需求"
    order.append(["客户简称", "产品编码", "产品型号", "需求数量", "接单日期", "发货日期", "客户优先级"])
    order.append(["测试", "A", "6201-2RS", quantity, datetime(2026, 4, 1), datetime(2026, 4, 3), 1])
    order.append(["测试", "B", "W6001", 17, datetime(2026, 4, 1), datetime(2026, 4, 3), 1])
    stock = book.create_sheet("产品及库存信息")
    stock.append(["产品编码", "产品型号", "库存数量"])
    stock.append(["A", "6201-2RS", 5])
    safety = book.create_sheet("安全库存标准")
    safety.append(["产品编码", "产品型号", "安全库存"])
    safety.append(["A", "6201-2RS", 8])
    lines = book.create_sheet("产线信息")
    lines.append(["线号", "可生产产品型号", "换型时间", None, "小时产能", "生产周期"])
    lines.append([None, None, "大换型", "小换型"])
    lines.append(["N001", "6201", None, 0.5, 1500, 4])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


class WorkbenchTests(TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.url = f"sqlite:///{(Path(tmp.name) / 'test.sqlite3').as_posix()}"
        client = TestClient(make_app(self.url, demo=True, embedded=False))
        self.client = client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        self.service = Service(self.url)
        self.addCleanup(self.service.engine.dispose)

    def api(self, method, path, status, **args):
        response = self.client.request(method, "/api/workbench" + path, **args)
        self.assertEqual(response.status_code, status, response.text[:400])
        return response

    def role(self, name):
        return self.api("POST", f"/demo/{name}", 200).json()

    def upload(self, data):
        return self.api("POST", "/datasets", 201, files={"file": ("fixture.xlsx", data)}).json()

    def request_plan(self, dataset, key="test-request-0001"):
        return self.api("POST", "/jobs", 202, json={
            "dataset_id": dataset, "at": "2026-04-01T00:00", "end": "2026-04-09T00:00", "request_key": key,
        }).json()

    def solve(self, key):
        request = self.service.claim()
        self.assertEqual(request["id"], key)
        self.service.process(request)
        payload = self.api("GET", f"/jobs/{key}", 200).json()
        self.assertEqual(payload["status"], "succeeded")
        return payload["output"]["plan_ids"][0]

    def advance(self, plan, action, revision, status, note=""):
        return self.api("POST", f"/plans/{plan}/transition", status,
                        json={"action": action, "revision": revision, "note": note})

    def test_authenticated_approval_delivery_and_idempotency(self):
        self.api("GET", "", 401)
        self.role("planner")
        content = workbook()
        snap = self.upload(content)
        self.assertEqual(snap["id"], self.upload(content)["id"])
        job = self.request_plan(snap["id"])
        self.assertEqual(self.request_plan(snap["id"])["id"], job["id"])
        plan = self.solve(job["id"])
        self.advance(plan, "submit", 0, 200)
        self.advance(plan, "approve", 1, 403)
        self.role("operator")
        self.api("GET", f"/plans/{plan}", 403)
        self.role("reviewer")
        self.advance(plan, "approve", 1, 200)
        self.advance(plan, "publish", 2, 422)
        self.advance(plan, "publish", 1, 409, "工厂复核")
        self.advance(plan, "publish", 2, 200, "未排由计划组确认")
        self.role("operator")
        final = self.advance(plan, "receive", 3, 200).json()
        self.assertEqual(final["status"], "received")
        self.assertEqual(self.api("GET", "", 200).json()["plant"]["active_plan"], plan)
        export = self.api("GET", f"/plans/{plan}/export.xlsx", 200)
        result = load_workbook(BytesIO(export.content), read_only=True)
        self.assertEqual(result.sheetnames, ["作业计划", "订单交付", "换型", "异常"])
        result.close()

    def test_old_snapshot_cannot_publish_and_other_plant_cannot_read(self):
        self.role("planner")
        old = self.upload(workbook())
        plan = self.solve(self.request_plan(old["id"])["id"])
        expected = self.api("GET", f"/plans/{plan}/orders/O00003/evidence", 200).json()
        self.assertEqual(expected["unassigned"][0]["qty"], 17)
        self.advance(plan, "submit", 0, 200)
        self.upload(workbook(120))
        self.role("reviewer")
        self.advance(plan, "approve", 1, 200)
        self.advance(plan, "publish", 2, 409, "已检查")
        self.service.seed_plant("other", "另一家工厂")
        self.service.add_user("other", "other-reviewer", "审批主管", "reviewer", "Temporarypassword1234")
        self.api("POST", "/session", 200, json={"username": "other-reviewer", "password": "Temporarypassword1234"})
        self.api("GET", f"/plans/{plan}", 404)
        self.api("GET", f"/datasets/{old['id']}/audit.xlsx", 404)

    def test_manual_edit_rejects_overlap_and_preserves_locked_job(self):
        self.role("planner")
        snap = self.upload(workbook())
        parent = self.solve(self.request_plan(snap["id"])["id"])
        origin = self.api("GET", f"/plans/{parent}", 200).json()
        order = next(item for item in origin["batches"] if item["job"] == "order:O00002")
        stock = next(item for item in origin["batches"] if item["job"] == "stock:A")
        endpoint = f"/plans/{parent}/batches/{order['key']}/edit"
        values = {"revision": 0, "line": "N001", "start": stock["start"], "locked": True}
        self.api("POST", endpoint, 422, json=values)
        values["start"] = "2026-04-02T08:00"
        draft = self.api("POST", endpoint, 201, json=values).json()
        self.assertEqual(draft["parent_id"], parent)
        anchor = next(item for item in draft["batches"] if item["job"] == order["job"])
        self.assertEqual((anchor["state"], anchor["start"]), ("locked", values["start"]))
        self.assertEqual(self.api("GET", f"/plans/{parent}", 200).json()["batches"], origin["batches"])
        self.api("POST", f"/plans/{draft['id']}/batches/{anchor['key']}/edit", 422,
                 json={**values, "start": "2026-04-03T08:00", "locked": False})
        job = self.api("POST", f"/plans/{draft['id']}/replan", 202, json={
            "at": origin["at"], "freeze_hours": 0, "request_key": "locked-replan-01",
            "events": [{"type": "insert", "code": "A", "model": "6201-2RS", "qty": 3, "due": "2026-04-02T00:00"}],
        }).json()
        result = self.api("GET", f"/plans/{self.solve(job['id'])}", 200).json()
        self.assertIn(anchor, result["batches"])
        self.assertTrue(any(item["order_id"] == "I00001" and item["start"] < anchor["start"]
                            for item in result["batches"]))
        endpoint = f"/plans/{draft['id']}/replan"
        case = {"at":"2026-04-02T07:00", "freeze_hours":0, "request_key":"locked-fault-01",
                "events":[{"type":"fault", "line":"N001", "duration_minutes":120}]}
        task = self.api("POST", endpoint, 202, json=case).json()
        self.service.process(self.service.claim())
        self.assertIn("已锁定", self.api("GET", f"/jobs/{task['id']}", 200).json()["error"])
        permissive = self.api("POST", endpoint, 202, json={
            **case, "request_key":"locked-fault-02", "release_conflicting_locks":True,
        }).json()
        fixed = self.api("GET", f"/plans/{self.solve(permissive['id'])}", 200).json()
        self.assertTrue(any("锁定因本次事件冲突已解除" in message for message in fixed["changes"]))

    def test_actual_execution_gates_replan_and_invalidates_outdated_draft(self):
        self.role("planner")
        plan_id = self.solve(self.request_plan(self.upload(workbook())["id"])["id"])
        plan = self.api("GET", f"/plans/{plan_id}", 200).json()
        work = next(item for item in plan["batches"] if item["order_id"] == "O00002")
        stock = next(item for item in plan["batches"] if item["job"] == "stock:A")
        report_path = f"/plans/{plan_id}/execution/{work['key']}"
        values = {"kind": "closed", "qty": 40, "start": work["start"], "end": work["end"], "revision": 0}
        self.api("PUT", report_path, 403, json=values)
        self.advance(plan_id, "submit", 0, 200)
        self.role("reviewer")
        self.advance(plan_id, "approve", 1, 200)
        self.advance(plan_id, "publish", 2, 200, "人工处置未排")
        self.role("operator")
        self.api("PUT", report_path, 409, json=values)
        self.advance(plan_id, "receive", 3, 200)
        self.role("planner")
        replan_path = f"/plans/{plan_id}/replan"
        request = {"at": stock["start"], "use_actual": True, "events": [], "request_key": "real-replan-01", "freeze_hours": 0}
        self.assertIn("待确认", self.api("POST", replan_path, 422, json=request).text)
        self.role("operator")
        self.api("PUT", report_path, 200, json={"kind":"not_started","qty":0,"revision":0,"checked_at":work["start"]})
        self.role("planner")
        self.assertIn("已过时", self.api("POST", replan_path, 422, json=request).text)
        self.role("operator")
        values["revision"] = 1
        self.api("PUT", report_path, 200, json=values)
        self.assertEqual(self.api("PUT", report_path, 200, json=values).json()["revision"], 2)
        self.role("planner")
        task = self.api("POST", replan_path, 202, json=request).json()
        result_id = self.solve(task["id"])
        result = self.api("GET", f"/plans/{result_id}", 200).json()
        self.assertEqual(result["execution_mode"], "actual")
        parts = [item for item in result["batches"] if item["job"] == work["job"]]
        self.assertEqual(sorted((item["state"], item["qty"]) for item in parts), [("actual", 40), ("planned", 55)])
        self.role("operator")
        self.api("PUT", report_path, 409, json={**values, "qty": 41})
        self.api("PUT", report_path, 200, json={**values, "qty": 41, "revision": 2})
        self.role("planner")
        self.assertEqual(self.api("POST", replan_path, 202, json=request).json()["id"], task["id"])
        self.advance(result_id, "submit", 0, 409)

    def test_recovered_job_rejects_stale_worker_results(self):
        self.role("planner")
        key = self.request_plan(self.upload(workbook())["id"])["id"]
        original = self.service.claim()
        with self.service.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.id == key).values(lease_until=0))
        new = self.service.claim()
        self.assertEqual(new["attempts"], 2)
        self.service.finish(original, error="Old worker result")
        self.assertEqual(self.api("GET", f"/jobs/{key}", 200).json()["status"], "running")
        self.service.process(new)
        self.assertEqual(self.api("GET", f"/jobs/{key}", 200).json()["status"], "succeeded")
