from collections import Counter, defaultdict
from dataclasses import dataclass
from hashlib import sha256

from .data import Dataset, Demand, InputError, Job, Line, Order, PRIORITY_WEIGHT, build_demand, timestamp


@dataclass(frozen=True, slots=True)
class Batch:
    job: str
    line: str
    qty: int
    setup: int
    start: int
    end: int
    status: str = "planned"


@dataclass(frozen=True, slots=True)
class Problem:
    dataset: Dataset
    demand: Demand
    origin: int
    at: int
    end: int
    stops: dict[str, tuple[tuple[int, int], ...]]
    holds: dict[str, int]
    pins: tuple[Batch, ...] = ()
    previous: tuple[Batch, ...] = ()
    changes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Plan:
    strategy: str
    batches: tuple[Batch, ...]
    unassigned: dict[str, int]


def batch_key(batch: Batch) -> str:
    return sha256(f"{batch.job}|{batch.line}|{batch.start}".encode()).hexdigest()[:24]


def first_problem(dataset: Dataset, at: int, end: int) -> Problem:
    if not at < end <= at + 366 * 1440:
        raise InputError("计划结束时间应晚于排产时点，规划窗口最长 366 天。")
    orders = tuple(order for order in dataset.orders if order.received <= at)
    if not orders:
        raise InputError("排产时点前没有已收到的订单。")
    return Problem(dataset, build_demand(dataset, orders, at, end), at, at, end, {}, {})


def setup_minutes(line: Line, before: Job | None, after: Job) -> int:
    if before is None or before.code == after.code:
        return 0
    if before.family == after.family:
        return line.minor
    if line.major is None:
        raise InputError(f"{line.id}：跨型号族生产需要大换型时长。")
    return line.major


def merge_windows(windows):
    result = []
    for start, end in sorted(windows):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def windows(problem: Problem, line: str):
    windows = merge_windows(problem.dataset.calendar.get(line, ((problem.origin, problem.end),)))
    closed = merge_windows(problem.stops.get(line, ()))
    for begin, finish in windows:
        begin, finish = max(begin, problem.origin), min(finish, problem.end)
        cursor = begin
        for start, end in closed:
            if end <= cursor:
                continue
            if start >= finish:
                break
            if cursor < start:
                yield cursor, start
            cursor = max(cursor, end)
        if cursor < finish:
            yield cursor, finish


def placed_quantities(batches):
    counts = Counter()
    for batch in batches:
        counts[batch.job] += batch.qty
    return counts


def missing_quantities(problem, batches):
    placed = placed_quantities(batches)
    return {job.id: job.qty - placed[job.id] for job in problem.demand.jobs.values()
            if job.qty > placed[job.id]}


def job_movement(problem, batches):
    def open_slots(items):
        slots = {}
        for batch in sorted(items, key=lambda row: row.start):
            if batch.start >= problem.at:
                slots.setdefault(batch.job, (batch.line, batch.start))
        return slots
    previous, current = open_slots(problem.previous), open_slots(batches)
    changed = sum(current.get(job) != slot for job, slot in previous.items())
    shift = sum(abs(current[job][1] - slot[1]) for job, slot in previous.items() if job in current)
    return changed, len(previous), shift


def measures(problem, batches, unassigned):
    job_map = problem.demand.jobs
    used, ends = Counter(), {}
    setups = 0
    for batch in batches:
        used[batch.line] += batch.end - batch.setup
        setups += batch.start - batch.setup
        ends[batch.job] = max(ends.get(batch.job, 0), batch.end)

    on_time = tardy = pending = order_missing = penalized_pending = 0
    for order in problem.demand.orders:
        job_id = f"order:{order.id}"
        if shortage := unassigned.get(job_id, 0):
            pending += 1
            order_missing += shortage
            penalized_pending += PRIORITY_WEIGHT[order.priority]
            continue
        completion = ends.get(job_id, max(problem.origin, order.received))
        on_time += completion <= order.due
        tardy += PRIORITY_WEIGHT[order.priority] * max(0, completion - order.due)
    stock_missing = sum(qty for job, qty in unassigned.items() if job_map[job].order_id is None)

    loads = []
    for line in problem.dataset.lines:
        capacity = sum(end - start for start, end in windows(problem, line))
        if capacity:
            loads.append(used[line] / capacity)
    mean = sum(loads) / len(loads) if loads else 0
    load_cv = (sum((x - mean) ** 2 for x in loads) / len(loads)) ** 0.5 / mean if mean else 0
    moved, comparable, shift = job_movement(problem, batches) if problem.previous else (0, 0, 0)
    return {
        "orders": len(problem.demand.orders), "on_time": on_time,
        "otd": on_time / len(problem.demand.orders), "weighted_tardy_hours": tardy / 60,
        "pending_orders": pending, "missing_order_units": order_missing, "pending_weight": penalized_pending,
        "shortfall_units": stock_missing, "setup_hours": setups / 60, "load_cv": load_cv,
        "makespan_hours": (max(ends.values(), default=problem.at) - problem.origin) / 60,
        "moved": moved, "comparable": comparable, "shift_hours": shift / 60,
    }


def report(problem: Problem, plan: Plan) -> dict:
    jobs = problem.demand.jobs
    placed = placed_quantities(plan.batches)
    ends = {}
    batches, orders, missing = [], [], []
    statuses = {"planned": "计划", "frozen": "冻结", "running": "执行中",
                "completed": "推定已完成", "interrupted": "故障前已完成",
                "actual": "实报完成", "locked": "已锁定"}
    for batch in sorted(plan.batches, key=lambda b: (b.line, b.start, b.job)):
        job = jobs[batch.job]
        ends[batch.job] = max(ends.get(batch.job, 0), batch.end)
        batches.append({"key": batch_key(batch), "line": batch.line, "job": batch.job, "order_id": job.order_id,
                        "code": job.code, "model": job.model, "qty": batch.qty,
                        "start": timestamp(batch.start), "end": timestamp(batch.end),
                        "setup_start": timestamp(batch.setup), "setup_minutes": batch.start - batch.setup,
                        "state": batch.status, "status": statuses[batch.status]})
    for order in problem.demand.orders:
        job_id = f"order:{order.id}"
        stock = problem.demand.allocated[order.id]
        planned = placed[job_id]
        completion = ends.get(job_id, max(problem.origin, order.received))
        incomplete = stock + planned < order.qty
        late = max(0, completion - order.due)
        status = "未排完" if incomplete else "延期" if late else "库存覆盖" if not planned else "按期"
        orders.append({"id": order.id, "customer": order.customer, "code": order.code, "qty": order.qty,
                       "stock": stock, "planned": planned, "due": timestamp(order.due),
                       "completion": None if incomplete else timestamp(completion),
                       "tardy_hours": None if incomplete else round(late / 60, 2), "status": status})
    for key, qty in plan.unassigned.items():
        job = jobs[key]
        if not any(job.family in line.families for line in problem.dataset.lines.values()):
            reason = "无适配产线，需人工确认"
        elif job.release >= problem.end:
            reason = "物料可用时点超出规划窗口"
        else:
            reason = "规划窗口内未找到可用加工区间"
        missing.append({"job": key, "order_id": job.order_id, "qty": qty, "code": job.code,
                        "model": job.model, "reason": reason})
    return {"strategy": plan.strategy, "at": timestamp(problem.at), "start": timestamp(problem.origin),
            "end": timestamp(problem.end), "lines": list(problem.dataset.lines),
            "metrics": measures(problem, plan.batches, plan.unassigned), "batches": batches,
            "orders": orders, "unassigned": missing, "changes": list(problem.changes)}
