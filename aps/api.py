import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from time import perf_counter
from typing import Annotated, Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .data import InputError, build_demand, data_summary, parse_timestamp
from .data import read_dataset as load_workbook
from .engine import solve
from .replan import plan_state, revised_problem
from .schedule import first_problem, report
from .store import Store
from .xlsx import audit_xlsx, plan_xlsx


WEB = Path(__file__).with_name("web")
MAX_UPLOAD = 20 * 1024 * 1024


class PlanningRequest(BaseModel):
    dataset_id: str
    at: str
    end: str


class Insert(BaseModel):
    type: Literal["insert"]
    code: str = Field(min_length=1, max_length=70)
    model: str = Field(min_length=1, max_length=100)
    customer: str = Field(default="紧急订单", max_length=70)
    qty: int = Field(gt=0, le=100_000_000)
    due: str
    priority: Literal[1, 2, 3] = 1


class Fault(BaseModel):
    type: Literal["fault"]
    line: str
    duration_minutes: int = Field(gt=0, le=43200)
    completed_qty: int | None = Field(default=None, ge=0)


class Delay(BaseModel):
    type: Literal["delay"]
    code: str
    available: str


Event = Annotated[Insert | Fault | Delay, Field(discriminator="type")]


class ReplanRequest(BaseModel):
    at: str
    freeze_hours: int = Field(default=8, ge=0, le=48)
    events: list[Event] = Field(min_length=1, max_length=20)


def make_app(db: Path) -> FastAPI:
    store = Store(db)

    @asynccontextmanager
    async def lifespan(app):
        store.initialize()
        yield

    app = FastAPI(title="新大地生产自动排程", lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.exception_handler(InputError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    def dataset_for(key):
        row = store.get_dataset(key)
        if row is None:
            raise HTTPException(404, "找不到该数据集。")
        return row[0], load_workbook(BytesIO(row[1]))

    def plan_for(key):
        row = store.get_plan(key)
        if row is None:
            raise HTTPException(404, "找不到该计划版本。")
        return row

    def full_demand(data):
        return build_demand(data, data.orders, min(row.received for row in data.orders),
                            max(row.due for row in data.orders))

    def show_dataset(key, name, data):
        return {"id": key, "name": name, "summary": data_summary(data, full_demand(data)),
                "lines": list(data.lines), "issues": [asdict(issue) for issue in data.issues]}

    def run_plans(dataset_id, problem, parent_id=None):
        baseline, plans, timings = solve(problem)
        results = [(plan_state(problem, plan), report(problem, plan)) for plan in (baseline, *plans)]
        store.save_plans(dataset_id, parent_id, results)
        return {"baseline": results[0][1], "plans": [result for _, result in results[1:]],
                **{key: round(value, 4) for key, value in timings.items()}}

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html")

    @app.post("/api/datasets", status_code=201)
    def upload(file: Annotated[UploadFile, File()]):
        name = (file.filename or "").replace("\\", "/").split("/")[-1]
        content = file.file.read(MAX_UPLOAD + 1)
        if not name.lower().endswith(".xlsx") or len(content) > MAX_UPLOAD:
            raise InputError("请选择 20 MB 以内的 .xlsx 工作簿。")
        data = load_workbook(BytesIO(content))
        key = store.create_dataset(name, content)
        return show_dataset(key, name, data)

    @app.get("/api/datasets/{key}")
    def get_dataset(key: str):
        name, data = dataset_for(key)
        return show_dataset(key, name, data)

    @app.get("/api/datasets/{key}/audit.xlsx")
    def export_audit(key: str):
        _, data = dataset_for(key)
        return excel(audit_xlsx(data, full_demand(data)), "newland-demand-audit.xlsx")

    @app.post("/api/plans", status_code=201)
    def create_plan(request: PlanningRequest):
        start = perf_counter()
        _, data = dataset_for(request.dataset_id)
        result = run_plans(request.dataset_id, first_problem(data, parse_timestamp(request.at),
                                                            parse_timestamp(request.end)))
        result["request_seconds"] = round(perf_counter() - start, 4)
        return result

    @app.post("/api/plans/{key}/replan", status_code=201)
    def replan(key: str, request: ReplanRequest):
        start = perf_counter()
        state, baseline = plan_for(key)
        dataset_id = baseline["dataset_id"]
        _, data = dataset_for(dataset_id)
        problem = revised_problem(data, state, parse_timestamp(request.at),
                                  [item.model_dump() for item in request.events], request.freeze_hours * 60)
        result = run_plans(dataset_id, problem, key)
        result["request_seconds"] = round(perf_counter() - start, 4)
        return result

    @app.get("/api/plans/{key}")
    def get_plan(key: str):
        return plan_for(key)[1]

    @app.get("/api/plans/{key}/export.xlsx")
    def export(key: str):
        return excel(plan_xlsx(plan_for(key)[1]), "newland-production-plan.xlsx")

    app.mount("/assets", StaticFiles(directory=WEB, check_dir=False), name="assets")
    return app


def excel(content, name):
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


app = make_app(Path(os.environ.get("APS_DB", ".var/aps.sqlite3")))
