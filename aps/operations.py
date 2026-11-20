from collections import defaultdict
from dataclasses import replace

from .data import InputError, Order, build_demand, parse_timestamp
from .schedule import Batch, Plan, Problem, batch_key, missing_quantities, setup_minutes, windows
from .validator import violations


def restore_problem(dataset, state):
    orders = tuple(Order(**item) for item in state["orders"])
    previous = tuple(Batch(**item) for item in state["batches"])
    priority = {o.id: 0 for o in orders if o.id.startswith("I") and o.priority == 1}
    demand = build_demand(dataset, orders, state["at"], state["end"], state["stock"], state["holds"], priority)
    return Problem(dataset, demand, state["origin"], state["at"], state["end"], state["stops"],
                   state["holds"], previous=previous)


def change_batch(dataset, state, key, line_id, start, lock):
    source = restore_problem(dataset, state)
    original = next((row for row in source.previous if batch_key(row) == key), None)
    if original is None:
        raise InputError("该版本中找不到此作业。")
    if original.status not in ("planned", "locked"):
        raise InputError("冻结、在制或已执行作业受保护。")
    moved = (line_id, start) != (original.line, original.start)
    if original.status == "locked" and moved:
        raise InputError("请先解锁作业，再修改时间或产线。")
    if not moved and (original.status == "locked") == lock:
        raise InputError("请修改产线、开工时间或锁定状态。")

    job = source.demand.jobs[original.job]
    line = dataset.lines.get(line_id)
    if not line or job.family not in line.families:
        raise InputError(f"{line_id} 无法加工 {job.model}。")
    if start < job.release:
        raise InputError("开工时间早于计划时点或物料就绪时间。")

    updated = replace(original, line=line_id, start=start, end=start + line.duration(original.qty),
                      status="locked" if lock else "planned")
    grouped = defaultdict(list)
    for item in source.previous:
        grouped[updated.line if item == original else item.line].append((updated if item == original else item, item == original))
    output = []
    for line_name, items in grouped.items():
        previous = None
        resource = dataset.lines[line_name]
        for item, changed in sorted(items, key=lambda pair: (pair[0].start, pair[0].job)):
            if changed or (moved and line_name in (line_id, original.line) and item.status == "planned"):
                required = setup_minutes(resource, source.demand.jobs[previous.job] if previous else None,
                                         source.demand.jobs[item.job])
                item = replace(item, setup=item.start - required)
                if changed and item.setup < source.at:
                    raise InputError("所需换型时间早于当前计划窗口。")
            output.append(item)
            previous = item

    pins = tuple(item for item in output if item.status != "planned")
    message = f"{original.job}：{'调整到 ' + line_id if moved else '更新锁定状态'}，{'已锁定' if lock else '可自动调整'}。"
    problem = replace(source, pins=pins, changes=(*state.get("changes", ()), message))
    result = Plan("人工调整", tuple(output), missing_quantities(problem, output))
    if errors := violations(problem, result):
        raise InputError("；".join(errors[:4]))
    return problem, result


def check_execution(state, key, values, entries):
    kind, qty = values["kind"], values["qty"]
    start_text, end_text = values["start"], values["end"]
    batch = next((item for item in map(lambda value: Batch(**value), state["batches"]) if batch_key(item) == key), None)
    if batch is None or batch.status == "actual":
        raise InputError("找不到可报工的作业，或该作业已作为历史记录保留。")
    if kind == "not_started":
        if qty or start_text or end_text:
            raise InputError("未开工记录的完成量应为 0，实际时间应留空。")
        checked_at = parse_timestamp(values["checked_at"])
        if checked_at < state["origin"]:
            raise InputError("确认未开工的时点须在计划窗口起点之后。")
        return batch, {"kind": kind, "qty": 0, "start": None, "end": None, "checked_at": checked_at}
    start, end = parse_timestamp(start_text), parse_timestamp(end_text)
    if not 0 <= qty <= batch.qty:
        raise InputError(f"实际完成量应在 0 至 {batch.qty} 件之间。")
    if not state["origin"] <= start < end:
        raise InputError("实际开工应在计划窗口起点之后，结束时间须晚于开工。")
    history = (Batch(**item) for item in state["batches"] if item["status"] == "actual")
    for row in history:
        if row.line == batch.line and start < row.end and row.start < end:
            raise InputError("实际时间与已保留的报工历史重叠。")
    for row in entries:
        actual = row["actual"]
        if (row["batch_key"] != key and row["line"] == batch.line and actual["kind"] == "closed"
                and start < actual["end"] and actual["start"] < end):
            raise InputError("同一产线已有作业在该实际时段内报工。")
    return batch, {"kind": kind, "qty": qty, "start": start, "end": end}


def execution_pins(batches, actual, at):
    pins, affected, pending = [], set(), []
    for row in batches:
        if row.status == "actual":
            pins.append(row)
            continue
        fact = actual.get(batch_key(row))
        if fact is None:
            if row.setup < at:
                pending.append(f"{row.line}/{row.job}")
            continue
        if fact["kind"] == "not_started":
            if row.setup < at:
                if fact.get("checked_at", -1) < at:
                    pending.append(f"{row.line}/{row.job}（未开工确认已过时）")
                else:
                    affected.add(row.line)
            continue
        if fact["end"] > at:
            raise InputError(f"{row.line}/{row.job} 的报工结束时间晚于本次重排时点。")
        pins.append(replace(row, qty=fact["qty"], setup=fact["start"], start=fact["start"],
                            end=fact["end"], status="actual"))
        if (fact["qty"], fact["start"], fact["end"]) != (row.qty, row.start, row.end):
            affected.add(row.line)
    if pending:
        raise InputError(f"截至重排时点有 {len(pending)} 道作业待确认：{'、'.join(pending[:4])}。"
                         "请在车间报工中填写实际完成量，或确认未开工。")
    return tuple(pins), affected
