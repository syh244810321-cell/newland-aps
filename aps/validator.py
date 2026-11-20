from collections import Counter, defaultdict

from .schedule import Plan, Problem, setup_minutes, windows


def violations(problem: Problem, plan: Plan) -> list[str]:
    jobs = problem.demand.jobs
    pins = set(problem.pins)
    findings = []
    quantities = Counter()
    by_line = defaultdict(list)
    batches = set(plan.batches)
    if not pins <= batches:
        findings.append("排程中遗漏或改动了冻结 / 已执行任务。")

    for row in plan.batches:
        job = jobs.get(row.job)
        line = problem.dataset.lines.get(row.line)
        if job is None or line is None:
            findings.append(f"{row.job}：未知任务或产线。")
            continue
        if row.qty < 0 or (row.qty == 0 and row.status != 'actual') or job.family not in line.families:
            findings.append(f"{row.job}：产量或产线适配错误。")
        if not problem.origin <= row.setup <= row.start < row.end <= problem.end:
            findings.append(f"{row.job}：作业起止时间错误。")
        if row not in pins and (row.status != "planned" or row.setup < problem.at or row.start < job.release):
            findings.append(f"{row.job}：作业早于计划时点或物料释放。")
        if row.status == "actual" and (row not in pins or row.end > problem.at):
            findings.append(f"{row.job}：报工必须为重排时点前的已确认记录。")
        if row.status not in ("interrupted", "actual") and row.end - row.start != line.duration(row.qty):
            findings.append(f"{row.job}：加工时长和产能不相符。")
        quantities[row.job] += row.qty
        by_line[row.line].append(row)

    for line_id, rows in by_line.items():
        prev = None
        line = problem.dataset.lines[line_id]
        available = tuple(windows(problem, line_id))
        for row in sorted(rows, key=lambda r: (r.setup, r.end)):
            if row.status != "actual" and not any(start <= row.setup and row.end <= end for start, end in available):
                findings.append(f"{row.job}：加工或换型超出产线可用区间。")
            if prev and prev.end > row.setup:
                findings.append(f"{row.job}：与 {prev.job} 时间重叠。")
            before = jobs[prev.job] if prev else None
            if row.status != "actual" and row.start - row.setup < setup_minutes(line, before, jobs[row.job]):
                findings.append(f"{row.job}：换型时间不足。")
            prev = row

    for job in jobs.values():
        missing = plan.unassigned.get(job.id, 0)
        if missing < 0 or quantities[job.id] + missing != job.qty:
            findings.append(f"{job.id}：安排件数与未排件数无法对账。")
    if set(plan.unassigned) - jobs.keys():
        findings.append("未排清单含未知任务。")
    return findings
