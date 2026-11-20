import os
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from threading import Event, Thread
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..api import MAX_UPLOAD, Event as PlanningEvent, ReplanRequest, excel
from ..data import InputError, build_demand, read_dataset
from ..xlsx import audit_xlsx, plan_xlsx
from .service import DEMO, SESSION_SECONDS, Service, WorkflowError


ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "frontend" / "dist"
COOKIE = "newland_workbench"


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class RunPlan(BaseModel):
    dataset_id: str
    at: str
    end: str
    request_key: str = Field(min_length=10, max_length=80)


class Rerun(ReplanRequest):
    request_key: str = Field(min_length=10, max_length=80)
    events: list[PlanningEvent] = Field(default_factory=list, max_length=20)
    use_actual: bool = False
    release_conflicting_locks: bool = False


class ManualChange(BaseModel):
    revision: int = Field(ge=0)
    line: str = Field(min_length=1, max_length=40)
    start: str
    locked: bool = False


class ActualReport(BaseModel):
    revision: int = Field(ge=0)
    kind: Literal["closed", "not_started"]
    qty: int = Field(ge=0)
    start: str | None = None
    end: str | None = None
    checked_at: str | None = None


class Transition(BaseModel):
    action: Literal["submit", "approve", "reject", "publish", "receive"]
    revision: int = Field(ge=0)
    note: str = Field(default="", max_length=500)


def make_app(db_url, *, demo=False, sample=None, secure_cookie=False, auto_schema=True, embedded=True):
    service = Service(db_url)

    @asynccontextmanager
    async def lifespan(app):
        stop = Event()
        worker = None
        try:
            if auto_schema:
                service.initialize()
            if demo:
                service.seed_demo()
            if embedded:
                worker = Thread(target=service.worker, args=(stop,), daemon=True)
                worker.start()
            yield
        finally:
            stop.set()
            if worker and worker.is_alive():
                worker.join(timeout=5)
            service.engine.dispose()

    app = FastAPI(title="新大地 APS 企业工作台", version="0.1.0", lifespan=lifespan, docs_url=None)

    @app.exception_handler(WorkflowError)
    @app.exception_handler(InputError)
    async def business_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=getattr(error, "status", 422))

    def session(request: Request):
        return service.authenticate(request.cookies.get(COOKIE))

    Actor = Depends(session)

    def login_cookie(request, response, user, secret=None, demo_role=False):
        token, actor = service.login(user, secret, demo_role)
        service.logout(request.cookies.get(COOKIE))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, samesite="strict", secure=secure_cookie)
        return actor

    @app.get("/api/workbench/config")
    def config():
        return {"demo": demo, "sample": bool(demo and sample and sample.is_file()),
                "roles": [{"id": role, "name": name} for role, name in DEMO] if demo else []}

    @app.post("/api/workbench/session")
    def sign_in(value: Login, request: Request, response: Response):
        return login_cookie(request, response, value.username.strip(), value.password)

    @app.post("/api/workbench/demo/{role}")
    def sign_in_demo(role: str, request: Request, response: Response):
        if not demo or role not in dict(DEMO):
            raise WorkflowError("演示登录不可用。", 404)
        return login_cookie(request, response, f"demo-{role}", demo_role=True)

    @app.get("/api/workbench/session")
    def who_am_i(actor=Actor):
        return actor

    @app.delete("/api/workbench/session", status_code=204)
    def sign_out(request: Request):
        service.logout(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/api/workbench")
    def overview(actor=Actor):
        return service.workspace(actor)

    @app.post("/api/workbench/datasets", status_code=201)
    def upload(file: Annotated[UploadFile, File()], actor=Actor):
        service.require(actor, "planner", "admin")
        name = (file.filename or "").replace("\\", "/").split("/")[-1]
        content = file.file.read(MAX_UPLOAD + 1)
        if not name.lower().endswith(".xlsx") or len(content) > MAX_UPLOAD or len(name) > 250:
            raise InputError("请选择 20 MB 以内的 .xlsx 工作簿。")
        return service.get_dataset(actor, service.add_dataset(actor, name, content))

    @app.post("/api/workbench/sample", status_code=201)
    def load_sample(actor=Actor):
        if not demo or actor["plant_id"] != "newland-demo" or not sample or not sample.is_file():
            raise WorkflowError("本机演示工作簿不可用。", 404)
        with sample.open("rb") as handle:
            content = handle.read(MAX_UPLOAD + 1)
        if len(content) > MAX_UPLOAD:
            raise InputError("演示工作簿超过 20 MB。")
        return service.get_dataset(actor, service.add_dataset(actor, sample.name, content))

    @app.get("/api/workbench/datasets/{key}/audit.xlsx")
    def export_audit(key: str, actor=Actor):
        service.require(actor, "planner", "reviewer", "admin")
        data = read_dataset(BytesIO(service.get_dataset(actor, key, raw=True)))
        demand = build_demand(data, data.orders, min(o.received for o in data.orders),
                              max(o.due for o in data.orders))
        return excel(audit_xlsx(data, demand), "newland-demand-audit.xlsx")

    @app.post("/api/workbench/jobs", status_code=202)
    def request_plan(value: RunPlan, actor=Actor):
        key = service.enqueue(actor, value.dataset_id, value.request_key, "solve",
                              {"at": value.at, "end": value.end})
        return service.get_job(actor, key)

    @app.post("/api/workbench/plans/{key}/replan", status_code=202)
    def request_replan(key: str, value: Rerun, actor=Actor):
        service.require(actor, "planner", "admin")
        plan = service.get_plan(actor, key)
        payload = value.model_dump(exclude={"request_key", "use_actual", "release_conflicting_locks"})
        if value.use_actual:
            payload["use_actual"] = True
        if value.release_conflicting_locks:
            payload["release_conflicting_locks"] = True
        task_id = service.enqueue(actor, plan["dataset_id"], value.request_key, "replan", {"parent_id": key, **payload})
        return service.get_job(actor, task_id)

    @app.get("/api/workbench/jobs/{key}")
    def job(key: str, actor=Actor):
        return service.get_job(actor, key)

    @app.get("/api/workbench/plans/{key}")
    def plan(key: str, actor=Actor):
        return service.get_plan(actor, key)

    @app.post("/api/workbench/plans/{key}/batches/{batch}/edit", status_code=201)
    def edit(key: str, batch: str, value: ManualChange, actor=Actor):
        return service.edit_batch(actor, key, batch, value.model_dump())

    @app.get("/api/workbench/plans/{key}/execution")
    def execution(key: str, actor=Actor):
        return service.list_execution(actor, key)

    @app.put("/api/workbench/plans/{key}/execution/{batch}")
    def save_execution(key: str, batch: str, value: ActualReport, actor=Actor):
        return service.record_execution(actor, key, batch, value.model_dump())

    @app.post("/api/workbench/plans/{key}/transition")
    def move_plan(key: str, value: Transition, actor=Actor):
        return service.transition(actor, key, value.action, value.revision, value.note.strip())

    @app.get("/api/workbench/plans/{key}/export.xlsx")
    def export_plan(key: str, actor=Actor):
        return excel(plan_xlsx(service.get_plan(actor, key)), "newland-production-plan.xlsx")

    @app.get("/api/workbench/plans/{key}/orders/{order_id}/evidence")
    def explain_order(key: str, order_id: str, actor=Actor):
        plan = service.get_plan(actor, key)
        row = next((item for item in plan["orders"] if item["id"] == order_id), None)
        if not row:
            raise WorkflowError("当前方案中找不到此订单。", 404)
        gaps = [item for item in plan["unassigned"] if item["order_id"] == order_id]
        batches = [item for item in plan["batches"] if item["order_id"] == order_id]
        return {"source": "经校验的排程版本", "plan_id": key, "order": row,
                "batches": batches, "unassigned": gaps}

    @app.get("/")
    def index():
        page = DIST / "index.html"
        if not page.is_file():
            return Response("请先在 frontend 目录运行 npm ci 和 npm run build。", 503, media_type="text/plain")
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False), name="product-assets")
    return app


def database_url():
    return os.environ.get("APS_PRODUCT_DATABASE", f"sqlite:///{(ROOT / '.var' / 'product.sqlite3').as_posix()}")


def from_env():
    filename = os.environ.get("APS_SAMPLE_XLSX")
    return make_app(database_url(), demo=os.environ.get("APS_PRODUCT_DEMO") == "1",
                    sample=Path(filename) if filename else None,
                    secure_cookie=os.environ.get("APS_SECURE_COOKIE") == "1",
                    auto_schema=os.environ.get("APS_AUTO_SCHEMA", "1") == "1",
                    embedded=os.environ.get("APS_EMBED_WORKER", "1") == "1")
