from collections import defaultdict
from dataclasses import asdict, replace

from .data import InputError, Order, build_demand, family, parse_timestamp
from .operations import execution_pins
from .schedule import Batch, Plan, Problem, batch_key, merge_windows, windows


def plan_state(problem: Problem, plan: Plan) -> dict:
    return {
        "at": problem.at, "origin": problem.origin, "end": problem.end, "stops": problem.stops,
        "changes": problem.changes,
        "holds": problem.holds, "stock": problem.demand.allocated,
        "orders": [asdict(order) for order in problem.demand.orders],
        "batches": [asdict(batch) for batch in plan.batches],
    }


def revised_problem(dataset, state, at, events, freeze_minutes=480, *, execution=None, release_conflicting_locks=False) -> Problem:
    if not state["at"] <= at < state["end"]:
        raise InputError("重排时点须在原计划时点与规划结束之间。")
    if not 0 <= freeze_minutes <= 48 * 60 or (not events and execution is None):
        raise InputError("至少提供一个事件，冻结时长应在 0–48 小时。")
    orders = {item["id"]: Order(**item) for item in state["orders"]}
    orders.update((order.id, order) for order in dataset.orders if order.received <= at)
    old_batches = tuple(Batch(**row) for row in state["batches"])
    stops = {line: [tuple(item) for item in spans] for line, spans in state["stops"].items()}
    holds = dict(state["holds"])
    changes = []
    new_faults = {}
    for event in events:
        kind = event["type"]
        if kind == "insert":
            batch_id = f"I{1 + sum(key.startswith('I') for key in orders):05d}"
            code, model = event["code"].strip(), event["model"].strip()
            due = parse_timestamp(event["due"])
            if due <= at or not code or not model:
                raise InputError("紧急插单需要产品编码、型号和晚于重排时点的交期。")
            existing = next((order.model for order in dataset.orders if order.code == code), None)
            if existing and family(existing) != family(model):
                raise InputError(f"{code}：插单型号族与订单主数据冲突。")
            earlier = next((order.model for order in orders.values() if order.code == code), None)
            if earlier and family(earlier) != family(model):
                raise InputError(f"{code}：插单型号族与该计划中的订单冲突。")
            orders[batch_id] = Order(batch_id, event["customer"].strip(), code, existing or earlier or model,
                                     event["qty"], at, due, event["priority"])
            changes.append(f"插单 {batch_id}：{code}，{event['qty']} 件。")
        elif kind == "fault":
            line = event["line"]
            if line not in dataset.lines or line in new_faults:
                raise InputError(f"{line}：故障产线不存在或在同批事件中重复。")
            end = at + event["duration_minutes"]
            stops.setdefault(line, []).append((at, end))
            new_faults[line] = event.get("completed_qty")
            changes.append(f"{line} 停机 {event['duration_minutes']} 分钟。")
        elif kind == "delay":
            code = event["code"].strip()
            release = parse_timestamp(event["available"])
            if release <= at or not any(order.code == code for order in orders.values()):
                raise InputError(f"{code}：物料延期需要当前计划中的编码和晚于重排时点的可用时间。")
            holds[code] = max(release, holds.get(code, at))
            changes.append(f"{code} 待加工任务推迟至物料可用时。")
        else:
            raise InputError("未知突发事件类型。")

    demand = build_demand(dataset, tuple(orders.values()), at, state["end"], state["stock"], holds,
                          {order.id: 0 for order in orders.values() if order.id.startswith("I") and order.priority == 1})
    base = Problem(dataset, demand, state["origin"], at, state["end"],
                   {line: tuple(merge_windows(spans)) for line, spans in stops.items()},
                   holds, previous=old_batches)
    allowed = {line: tuple(windows(base, line)) for line in dataset.lines}
    pins = []
    opened_lines = set()
    if execution is not None:
        actual, opened_lines = execution_pins(old_batches, execution, at)
        pins.extend(actual)
        changes.append(f"已基于车间报工保留 {len(actual)} 道实报记录，未完成量重新排程。")
    for batch in sorted(old_batches, key=lambda row: (row.line, row.setup)):
        job = demand.jobs[batch.job]
        if execution is not None:
            fact = execution.get(batch_key(batch))
            if batch.status == "actual" or (fact and (fact["kind"] == "closed" or batch.setup < at)):
                continue
        if batch.end <= at:
            status = batch.status if batch.status in ("interrupted", "actual") else "completed"
            pins.append(replace(batch, status=status))
            continue
        overlaps_fault = (batch.line in new_faults and
                          not any(start <= batch.setup and batch.end <= end for start, end in allowed[batch.line]))
        if overlaps_fault and batch.start < at < batch.end:
            done = new_faults[batch.line]
            if done is None:
                raise InputError(f"{batch.line} 的 {batch.job} 正在加工，请提供停机前实际完成件数。")
            if not 0 <= done <= batch.qty:
                raise InputError(f"{batch.line} 的实际完成件数须在 0 至 {batch.qty} 之间。")
            if done:
                pins.append(replace(batch, qty=done, end=at, status="interrupted"))
            opened_lines.add(batch.line)
            changes.append(f"{batch.job} 保留故障前 {done} 件，剩余 {batch.qty - done} 件重新安排。")
            continue
        invalid_hold = batch.start >= at and holds.get(job.code, at) > batch.start
        if batch.status == "locked" and batch.setup >= at:
            if not (overlaps_fault or invalid_hold):
                pins.append(batch)
                continue
            if not release_conflicting_locks:
                raise InputError(f"{batch.line}/{batch.job} 已锁定且与事件冲突，请确认是否解除本次冲突锁定。")
            opened_lines.add(batch.line)
            changes.append(f"{batch.line}/{batch.job} 的锁定因本次事件冲突已解除，原发布版保留。")
            continue
        if overlaps_fault or invalid_hold:
            if batch.setup < at + freeze_minutes:
                changes.append(f"{batch.line} 的 {batch.job} 受事件影响，解除该处及其后续冻结。")
            opened_lines.add(batch.line)
            continue
        if batch.setup < at:
            pins.append(replace(batch, status="running"))
        elif batch.setup < at + freeze_minutes and batch.line not in opened_lines:
            pins.append(replace(batch, status="frozen"))
    return replace(base, pins=tuple(pins), changes=tuple(changes))
