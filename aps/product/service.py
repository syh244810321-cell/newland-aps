import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import asdict
from io import BytesIO
from threading import Event
from time import time
from uuid import uuid4

from sqlalchemy import and_, create_engine, delete, event, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..data import InputError, build_demand, data_summary, parse_timestamp, read_dataset, timestamp
from ..engine import solve
from ..operations import change_batch, check_execution, execution_pins
from ..replan import plan_state, revised_problem
from ..schedule import Batch, batch_key, first_problem, report
from .tables import audit, datasets, executions, jobs, metadata, plans, plants, sessions, users


log = logging.getLogger(__name__)
ROLES = ("planner", "reviewer", "operator", "admin")
DEMO = (("planner", "计划员"), ("reviewer", "审批主管"), ("operator", "车间班组"))
ACTIONS = {
    "submit": ("draft", "review", {"planner", "admin"}),
    "approve": ("review", "approved", {"reviewer", "admin"}),
    "reject": ("review", "draft", {"reviewer", "admin"}),
    "publish": ("approved", "published", {"reviewer", "admin"}),
    "receive": ("published", "received", {"operator", "admin"}),
}
SESSION_SECONDS = 8 * 60 * 60
LEASE_SECONDS = 120


class WorkflowError(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def ident():
    return uuid4().hex


def digest(data):
    return hashlib.sha256(data).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000)
    return f"{salt}:{hashed.hex()}"


def inspect_dataset(blob):
    data = read_dataset(BytesIO(blob))
    demand = build_demand(data, data.orders, min(o.received for o in data.orders), max(o.due for o in data.orders))
    return {"summary": data_summary(data, demand), "lines": list(data.lines),
            "issues": [asdict(issue) for issue in data.issues],
            "resources": [{"id": line.id, "families": sorted(line.families), "capacity": line.capacity,
                           "major": line.major, "minor": line.minor, "cycle": line.cycle,
                           "calendar": bool(data.calendar.get(line.id))} for line in data.lines.values()]}


class Service:
    def __init__(self, url):
        options = {"connect_args": {"timeout": 15}} if url.startswith("sqlite:") else {"pool_pre_ping": True}
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", self.configure_sqlite)

    @staticmethod
    def configure_sqlite(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    def initialize(self):
        if self.engine.dialect.name == "sqlite":
            with self.engine.connect() as conn:
                conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        metadata.create_all(self.engine)

    def seed_plant(self, plant_id, name):
        with self.engine.begin() as conn:
            if not conn.execute(select(plants.c.id).where(plants.c.id == plant_id)).first():
                conn.execute(insert(plants).values(id=plant_id, name=name))

    def add_user(self, plant_id, username, name, role, password=None):
        if role not in ROLES:
            raise WorkflowError("未知角色。", 422)
        try:
            with self.engine.begin() as conn:
                conn.execute(insert(users).values(id=ident(), plant_id=plant_id, username=username, name=name,
                                                  role=role, password=password_hash(password) if password else None))
        except IntegrityError as exc:
            raise WorkflowError("该用户名已存在。") from exc

    def seed_demo(self):
        self.seed_plant("newland-demo", "新大地 · 试点工厂")
        for role, name in DEMO:
            try:
                self.add_user("newland-demo", f"demo-{role}", name, role)
            except WorkflowError:
                pass

    def login(self, username, password=None, demo=False):
        with self.engine.begin() as conn:
            actor = conn.execute(select(users).where(users.c.username == username)).mappings().first()
            if demo:
                allowed = actor and actor["plant_id"] == "newland-demo" and actor["password"] is None
            else:
                saved = actor["password"] if actor and actor["password"] else ""
                computed = password_hash(password or "", saved.split(":")[0] if saved else "00" * 16)
                allowed = bool(saved and hmac.compare_digest(saved, computed))
            if not allowed:
                raise WorkflowError("登录凭据无效。", 401)
            token = secrets.token_urlsafe(32)
            conn.execute(delete(sessions).where(sessions.c.expires < time()))
            conn.execute(insert(sessions).values(key=digest(token.encode()), user_id=actor["id"],
                                                 expires=time() + SESSION_SECONDS))
            return token, self.public_user(actor)

    def authenticate(self, token):
        if not token:
            raise WorkflowError("请先登录。", 401)
        with self.engine.connect() as conn:
            actor = conn.execute(select(users).join(sessions).where(
                sessions.c.key == digest(token.encode()), sessions.c.expires > time())).mappings().first()
        if not actor:
            raise WorkflowError("会话已过期，请重新登录。", 401)
        return self.public_user(actor)

    def logout(self, token):
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(sessions).where(sessions.c.key == digest(token.encode())))

    @staticmethod
    def public_user(actor):
        return {key: actor[key] for key in ("id", "plant_id", "name", "role")}

    @staticmethod
    def require(actor, *roles):
        if actor["role"] not in roles:
            raise WorkflowError("当前角色无权执行此操作。", 403)

    @staticmethod
    def event(conn, actor, object_id, event, note=""):
        conn.execute(insert(audit).values(id=ident(), plant_id=actor["plant_id"], user_id=actor["id"],
                                          object_id=object_id, event=event, note=note, created=time()))

    def add_dataset(self, actor, name, blob):
        self.require(actor, "planner", "admin")
        info = inspect_dataset(blob)
        fingerprint = digest(blob)
        plant_id = actor["plant_id"]
        with self.engine.begin() as conn:
            conn.execute(select(plants.c.id).where(plants.c.id == plant_id).with_for_update())
            existing = conn.execute(select(datasets.c.id).where(
                datasets.c.plant_id == plant_id, datasets.c.digest == fingerprint)).scalar()
            if existing:
                return existing
            key = ident()
            try:
                with conn.begin_nested():
                    conn.execute(insert(datasets).values(id=key, plant_id=plant_id, name=name,
                                 digest=fingerprint, workbook=blob, created=time(), info=info))
            except IntegrityError:
                return conn.execute(select(datasets.c.id).where(
                    datasets.c.plant_id == plant_id, datasets.c.digest == fingerprint)).scalar_one()
            conn.execute(update(plants).where(plants.c.id == plant_id).values(latest_dataset=key))
            self.event(conn, actor, key, "import", name)
            return key

    @staticmethod
    def scoped(conn, table, plant_id, key):
        row = conn.execute(select(table).where(table.c.plant_id == plant_id, table.c.id == key)).mappings().first()
        if not row:
            raise WorkflowError("当前工厂中找不到该记录。", 404)
        return row

    @staticmethod
    def dataset_view(row):
        return {"id": row["id"], "name": row["name"], "created": row["created"], **row["info"]}

    def get_dataset(self, actor, key, raw=False):
        with self.engine.connect() as conn:
            row = self.scoped(conn, datasets, actor["plant_id"], key)
        return row["workbook"] if raw else self.dataset_view(row)

    @staticmethod
    def plan_view(row, full=False):
        view = {key: row[key] for key in ("id", "job_id", "dataset_id", "parent_id", "strategy",
                                         "status", "revision", "created", "submitted_by")}
        result = row["result"]
        if full:
            view.update(result)
            if result["batches"] and "key" not in result["batches"][0]:
                keys = {(b.job, b.line, timestamp(b.start)): batch_key(b)
                        for b in (Batch(**item) for item in row["state"]["batches"])}
                view["batches"] = [dict(item, key=keys[item["job"], item["line"], item["start"]])
                                   for item in result["batches"]]
        else:
            view.update({key: result[key] for key in ("metrics", "at", "end")})
        view['execution_mode'] = (row['state'].get('execution_basis') or {}).get('mode')
        return view

    def get_plan(self, actor, key):
        with self.engine.connect() as conn:
            row = self.scoped(conn, plans, actor["plant_id"], key)
            if actor["role"] == "operator" and row["status"] not in ("published", "received"):
                raise WorkflowError("此计划尚未发布。", 403)
        return self.plan_view(row, full=True)

    def workspace(self, actor):
        plant_id = actor["plant_id"]
        with self.engine.connect() as conn:
            plant = conn.execute(select(plants).where(plants.c.id == plant_id)).mappings().one()
            dataset = None
            if plant["latest_dataset"] and actor["role"] != "operator":
                dataset = self.dataset_view(self.scoped(conn, datasets, plant_id, plant["latest_dataset"]))
            selection = select(plans).where(plans.c.plant_id == plant_id)
            history = select(audit.c.event, audit.c.note, audit.c.created, audit.c.object_id, users.c.name).join(
                users, audit.c.user_id == users.c.id).where(audit.c.plant_id == plant_id)
            if actor["role"] == "operator":
                selection = selection.where(plans.c.status.in_(["published", "received"]))
                history = history.where(audit.c.event.in_(["publish", "receive", "report-execution"]))
            recent = conn.execute(selection.order_by(plans.c.created.desc(), plans.c.strategy).limit(36)).mappings()
            activity = conn.execute(history.order_by(audit.c.created.desc()).limit(30)).mappings()
            return {"plant": dict(plant), "dataset": dataset, "plans": [self.plan_view(row) for row in recent],
                    "activity": [dict(row) for row in activity]}

    @staticmethod
    def execution_rows(conn, plant_id, plan_id):
        return list(conn.execute(select(executions).where(
            executions.c.plant_id == plant_id, executions.c.plan_id == plan_id)).mappings())

    @staticmethod
    def execution_snapshot(rows):
        return {row["batch_key"]: {"actual": row["actual"], "revision": row["revision"]} for row in rows}

    @staticmethod
    def fingerprint(snapshot):
        return digest(json.dumps(snapshot, sort_keys=True).encode())

    @staticmethod
    def lock_plant(conn, plant_id):
        if conn.dialect.name == "sqlite":
            conn.execute(update(plants).where(plants.c.id == plant_id).values(name=plants.c.name))
        return conn.execute(select(plants).where(plants.c.id == plant_id).with_for_update()).mappings().one()

    def edit_batch(self, actor, plan_id, key, values):
        self.require(actor, "planner", "admin")
        site = actor["plant_id"]
        with self.engine.begin() as conn:
            self.lock_plant(conn, site)
            row = self.scoped(conn, plans, site, plan_id)
            if row["status"] != "draft" or row["revision"] != values["revision"]:
                raise WorkflowError("请在仍处于草案状态的版本上调整。")
            data = self.scoped(conn, datasets, site, row["dataset_id"])
            problem, plan = change_batch(read_dataset(BytesIO(data["workbook"])), row["state"], key,
                                         values["line"], parse_timestamp(values["start"]), values["locked"])
            state = plan_state(problem, plan)
            if "execution_basis" in row["state"]:
                state["execution_basis"] = row["state"]["execution_basis"]
            new = dict(id=ident(), plant_id=site, dataset_id=row["dataset_id"], job_id=row["job_id"],
                       parent_id=plan_id, strategy=plan.strategy, created=time(), status="draft", revision=0,
                       state=state, result=report(problem, plan), submitted_by=None)
            conn.execute(insert(plans).values(**new))
            self.event(conn, actor, new["id"], "manual-adjust", problem.changes[-1])
            return self.plan_view(new, full=True)

    def list_execution(self, actor, plan_id):
        self.get_plan(actor, plan_id)
        with self.engine.connect() as conn:
            rows = self.execution_rows(conn, actor["plant_id"], plan_id)
        return [self.execution_view(row) for row in rows]

    @staticmethod
    def execution_view(row):
        actual = row["actual"]
        return {**actual, "batch_key": row["batch_key"], "line": row["line"],
                "start": timestamp(actual["start"]) if actual["start"] is not None else None,
                "end": timestamp(actual["end"]) if actual["end"] is not None else None,
                "checked_at": timestamp(actual["checked_at"]) if actual.get("checked_at") is not None else None,
                "revision": row["revision"]}

    def record_execution(self, actor, plan_id, key, values):
        self.require(actor, "operator", "admin")
        site = actor["plant_id"]
        with self.engine.begin() as conn:
            plant = self.lock_plant(conn, site)
            row = self.scoped(conn, plans, site, plan_id)
            if plant["active_plan"] != plan_id or row["status"] != "received":
                raise WorkflowError("只能为当前已签收的计划登记报工。")
            entries = self.execution_rows(conn, site, plan_id)
            earlier = next((item for item in entries if item["batch_key"] == key), None)
            batch, actual = check_execution(row["state"], key, values, entries)
            revision = earlier["revision"] if earlier else 0
            if earlier and actual == earlier["actual"]:
                return self.execution_view(earlier)
            if values["revision"] != revision:
                raise WorkflowError("报工已更新，请刷新并核对。")
            attrs = {"actual": actual, "revision": revision + 1, "user_id": actor["id"], "created": time()}
            if earlier:
                conn.execute(update(executions).where(executions.c.id == earlier["id"]).values(**attrs))
                note = f"{batch.line}/{batch.job}；结批记录 {earlier['actual']} → {actual}"
            else:
                conn.execute(insert(executions).values(id=ident(), plant_id=site, plan_id=plan_id,
                             batch_key=key, line=batch.line, **attrs))
                note = f"{batch.line}/{batch.job}；{'未开工' if actual['kind'] == 'not_started' else '实报完成 ' + str(actual['qty']) + ' 件'}"
            self.event(conn, actor, plan_id, "report-execution", note)
            return self.execution_view(dict(attrs, batch_key=key, line=batch.line))

    def enqueue(self, actor, dataset_id, request_key, kind, payload):
        self.require(actor, "planner", "admin")
        site = actor["plant_id"]
        with self.engine.begin() as conn:
            self.scoped(conn, datasets, site, dataset_id)
            if kind == "replan":
                parent = self.scoped(conn, plans, site, payload["parent_id"])
                if parent["dataset_id"] != dataset_id:
                    raise WorkflowError("重排必须引用基准版本的输入快照。")
            criteria = (jobs.c.plant_id == site, jobs.c.request_key == request_key)
            previous = conn.execute(select(jobs).where(*criteria)).mappings().first()
            if not previous:
                if kind == "replan" and payload.get("use_actual"):
                    plant = conn.execute(select(plants).where(plants.c.id == site)).mappings().one()
                    if plant["active_plan"] != parent["id"] or parent["status"] != "received":
                        raise WorkflowError("按报工重排须以当前已签收的计划为基准。")
                    snapshot = self.execution_snapshot(self.execution_rows(conn, site, parent["id"]))
                    batches = (Batch(**item) for item in parent["state"]["batches"])
                    execution_pins(batches, {key: fact["actual"] for key, fact in snapshot.items()},
                                   parse_timestamp(payload["at"]))
                    payload = dict(payload, _execution=snapshot)
                now = time()
                key = ident()
                try:
                    with conn.begin_nested():
                        conn.execute(insert(jobs).values(id=key, plant_id=site, dataset_id=dataset_id,
                                     request_key=request_key, kind=kind, payload=payload, status="queued",
                                     actor=actor["id"], created=now, updated=now, attempts=0, lease_until=0))
                        self.event(conn, actor, key, kind)
                    return key
                except IntegrityError:
                    previous = conn.execute(select(jobs).where(*criteria)).mappings().one()
            saved = {key: value for key, value in previous["payload"].items() if key != "_execution"}
            intent = {key: value for key, value in payload.items() if key != "_execution"}
            if (previous["dataset_id"], previous["kind"], saved) != (dataset_id, kind, intent):
                raise WorkflowError("相同请求键已用于不同的排程请求。")
            return previous["id"]

    def get_job(self, actor, key):
        self.require(actor, "planner", "admin")
        with self.engine.connect() as conn:
            row = self.scoped(conn, jobs, actor["plant_id"], key)
        return {k: row[k] for k in ("id", "kind", "status", "attempts", "output", "error")}

    def claim(self):
        now = time()
        with self.engine.begin() as conn:
            conn.execute(update(jobs).where(jobs.c.status == "running", jobs.c.lease_until < now,
                         jobs.c.attempts >= 3).values(status="failed", error="任务重试已用完。", updated=now))
            available = or_(jobs.c.status == "queued",
                            and_(jobs.c.status == "running", jobs.c.lease_until < now, jobs.c.attempts < 3))
            task = conn.execute(select(jobs).where(available).order_by(jobs.c.created).limit(1)
                                .with_for_update(skip_locked=True)).mappings().first()
            if task:
                changed = conn.execute(update(jobs).where(jobs.c.id == task["id"], jobs.c.attempts == task["attempts"],
                                       available).values(status="running", attempts=task["attempts"] + 1,
                                                         lease_until=now + LEASE_SECONDS, updated=now))
                if changed.rowcount:
                    return dict(task, attempts=task["attempts"] + 1)
        return None

    def process(self, task):
        started = time()
        payload = task["payload"]
        parent_id = payload.get("parent_id")
        try:
            with self.engine.connect() as conn:
                snapshot = self.scoped(conn, datasets, task["plant_id"], task["dataset_id"])
                parent = self.scoped(conn, plans, task["plant_id"], parent_id)["state"] if parent_id else None
            data = read_dataset(BytesIO(snapshot["workbook"]))
            at = parse_timestamp(payload["at"])
            snapshot = payload.get("_execution")
            facts = {key: fact["actual"] for key, fact in snapshot.items()} if snapshot is not None else None
            problem = (revised_problem(data, parent, at, payload["events"], payload["freeze_hours"] * 60,
                                       execution=facts, release_conflicting_locks=payload.get("release_conflicting_locks", False))
                       if parent else first_problem(data, at, parse_timestamp(payload["end"])))
            baseline, candidates, timings = solve(problem)
            rows = []
            for plan in (baseline, *candidates):
                state = plan_state(problem, plan)
                if parent:
                    state["execution_basis"] = {"mode": "actual", "plan": parent_id,
                                                "fingerprint": self.fingerprint(snapshot)} if snapshot is not None else {"mode": "simulation"}
                rows.append((ident(), plan, state, report(problem, plan)))
        except (InputError, WorkflowError) as exc:
            self.finish(task, error=str(exc))
        except Exception:
            log.exception("Planning job failed %s", task["id"])
            self.finish(task, error="求解失败，可重新发起，或联系管理员检查日志。")
        else:
            self.finish(task, rows, {**timings, "request_seconds": time() - started}, parent_id)

    def finish(self, task, rows=(), timings=None, parent_id=None, error=None):
        with self.engine.begin() as conn:
            output = ({"plan_ids": [row[0] for row in rows], "timings": timings} if not error else None)
            updated = conn.execute(update(jobs).where(jobs.c.id == task["id"], jobs.c.status == "running",
                                   jobs.c.attempts == task["attempts"]).values(
                status="failed" if error else "succeeded", output=output, error=error, updated=time(), lease_until=0))
            if updated.rowcount and rows:
                finished = time()
                for index, (key, plan, state, result) in enumerate(rows):
                    conn.execute(insert(plans).values(id=key, plant_id=task["plant_id"], dataset_id=task["dataset_id"],
                                 job_id=task["id"], parent_id=parent_id, strategy=plan.strategy,
                                 created=finished - index / 1_000_000, status="draft", revision=0, state=state, result=result))

    def worker(self, stop: Event):
        while not stop.is_set():
            try:
                task = self.claim()
                if task:
                    self.process(task)
                    continue
            except Exception:
                log.exception("Could not claim or finish a planning job")
            stop.wait(0.3)

    def transition(self, actor, plan_id, action, revision, note):
        before, after, roles = ACTIONS[action]
        self.require(actor, *roles)
        with self.engine.begin() as conn:
            plant_id = actor["plant_id"]
            plant = self.lock_plant(conn, plant_id)
            row = self.scoped(conn, plans, plant_id, plan_id)
            if row["status"] != before or row["revision"] != revision:
                raise WorkflowError("方案状态已变化，请刷新。")
            if action in ("approve", "reject") and row["submitted_by"] == actor["id"]:
                raise WorkflowError("请由另一位有权限的人员审核。", 403)
            if action in ("submit", "publish") and row["dataset_id"] != plant["latest_dataset"]:
                raise WorkflowError("数据快照已更新，请基于最新数据重新排程。")
            basis = row["state"].get("execution_basis") or {}
            if action == "publish" and basis.get("mode") == "simulation" and plant_id != "newland-demo":
                raise WorkflowError("模拟重排依据推定进度，请基于车间报工重新生成生产计划。")
            if action in ("submit", "publish") and basis.get("mode") == "actual":
                source = basis["plan"]
                snapshot = self.execution_snapshot(self.execution_rows(conn, plant_id, source))
                if plant["active_plan"] != source or self.fingerprint(snapshot) != basis["fingerprint"]:
                    raise WorkflowError("发布版本或报工已有更新，请重新生成重排。")
            if action == "publish" and row["result"]["unassigned"] and not note:
                raise WorkflowError("方案含未排需求，发布前请记录处置说明。", 422)
            if action == "receive" and plant["active_plan"] != plan_id:
                raise WorkflowError("该计划已被其他发布版本替代。")
            attrs = {"status": after, "revision": revision + 1}
            if action == "submit":
                attrs["submitted_by"] = actor["id"]
            changed = conn.execute(update(plans).where(plans.c.id == plan_id, plans.c.revision == revision,
                                   plans.c.status == before).values(**attrs))
            if not changed.rowcount:
                raise WorkflowError("方案状态已变化，请刷新。")
            if action == "publish":
                conn.execute(update(plants).where(plants.c.id == plant_id).values(active_plan=plan_id))
            self.event(conn, actor, plan_id, action, note)
            return self.plan_view(dict(row, **attrs), full=True)
