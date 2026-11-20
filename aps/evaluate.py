import argparse
import json
import platform
from math import ceil
from pathlib import Path
from statistics import median

from .data import read_dataset, timestamp
from .engine import solve
from .replan import plan_state, revised_problem
from .schedule import first_problem, report
from .xlsx import plan_xlsx


def scenario_cases(problem, baseline):
    origin = problem.at
    relevant = [job for job in problem.demand.jobs.values() if job.order_id and
                sum(job.family in line.families for line in problem.dataset.lines.values()) >= 2]
    for index in range(5):
        at = origin + (2 + index * 12) * 60
        order = relevant[(index + 2) * len(relevant) // 8]
        insert = {"type": "insert", "code": order.code, "model": order.model,
                  "customer": "模拟客户", "qty": 2000 + index * 1250,
                  "due": timestamp(at + (12 + index * 6) * 60), "priority": 1}
        delay = {"type": "delay", "code": order.code, "available": timestamp(at + (36 + 12 * index) * 60)}
        active = [row for row in baseline.batches if row.start < at < row.end]
        if active:
            row = sorted(active, key=lambda item: (item.line, item.job))[index % len(active)]
            capacity = problem.dataset.lines[row.line].capacity
            qty = min(row.qty - 1, (at - row.start) * capacity // 60)
            fault_line = row.line
        else:
            qty = None
            fault_line = sorted(problem.dataset.lines)[index]
        fault = {"type": "fault", "line": fault_line, "duration_minutes": (2 + 3 * index) * 60,
                 "completed_qty": qty}
        for name, events in [("插单", [insert]), ("故障", [fault]), ("物料延期", [delay]),
                             ("组合", [insert, fault, delay])]:
            yield {"id": f"{name}-{index + 1:02}", "kind": name, "synthetic": True,
                   "at": at, "at_local": timestamp(at), "freeze_hours": 8, "events": events}


def summarize(problem, plan):
    result = report(problem, plan)
    return {"strategy": plan.strategy, **result["metrics"]}


def evaluate(workbook, output):
    data = read_dataset(workbook)
    problem = first_problem(data, max(o.received for o in data.orders), max(o.due for o in data.orders) + 10081)
    baseline, candidates, initial = solve(problem)
    results = []
    times = [initial, *(solve(problem)[2] for _ in range(9))]
    source = plan_state(problem, baseline)
    scenarios = list(scenario_cases(problem, baseline))
    for case in scenarios:
        after = revised_problem(data, source, case["at"], case["events"], case["freeze_hours"] * 60)
        rule, options, timing = solve(after)
        results.append({"id": case["id"], "rule": summarize(after, rule),
                        "candidates": [summarize(after, plan) for plan in options], "timing": timing})
    findings = {"environment": platform.platform(), "python": platform.python_version(),
                "as_of": timestamp(problem.at), "plan_end": timestamp(problem.end),
                "orders": len(problem.demand.orders), "lines": len(data.lines), "timing_samples": times,
                "baseline": summarize(problem, baseline), "candidates": [summarize(problem, p) for p in candidates],
                "scenarios": results}
    output.mkdir(parents=True, exist_ok=True)
    for name, value in (("突发场景库.json", {"base_strategy": baseline.strategy, "start": timestamp(problem.at),
                                        "end": timestamp(problem.end), "scenarios": scenarios}),
                        ("实验结果.json", findings)):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "规则基准排产.xlsx").write_bytes(plan_xlsx(report(problem, baseline)))
    (output / "示例优化排产.xlsx").write_bytes(plan_xlsx(report(problem, candidates[0])))
    (output / "实验与验收报告.md").write_text(_markdown(findings), encoding="utf-8")
    return findings


def _markdown(result):
    timing = sorted(item["solver_seconds"] for item in result["timing_samples"])
    first = [item["first_feasible_seconds"] for item in result["timing_samples"]]
    p95 = timing[ceil(.95 * len(timing)) - 1]
    scenario_timing = [row["timing"]["solver_seconds"] for row in result["scenarios"]]
    results = [result["baseline"], *result["candidates"]]
    text = [
        "# 新大地生产排程实验与验收报告", "",
        f"{result['environment']}，Python {result['python']}。截至 {result['as_of']}，"
        f"规划至 {result['plan_end']}，含 {result['orders']} 条订单、{result['lines']} 条产线。", "",
        f"完整数据重复 {len(timing)} 次，首批可行规则计划中位耗时 {median(first):.3f} 秒，"
        f"整批候选中位耗时 {median(timing):.3f} 秒，P95 {p95:.3f} 秒。"
        "计时涵盖求解和独立校验，网页另行记录含导入和写入的请求耗时。", "",
        "## 完整数据方案比较", "",
        "| 方案 | 准交率 | 加权延期 h | 换型 h | 负载 CV | 待排订单 | 未排订单件数 | 补库缺口 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in results:
        text.append(f"| {row['strategy']} | {row['otd']:.2%} | {row['weighted_tardy_hours']:.1f} |"
                    f" {row['setup_hours']:.1f} | {row['load_cv']:.3f} | {row['pending_orders']} |"
                    f" {row['missing_order_units']} | {row['shortfall_units']} |")
    text.extend(["", "## 突发重排", "",
                 f"{len(result['scenarios'])} 组事件使用原始订单数据构造。事件和停机前产量是模拟值。"
                 f"整批重排求解中位耗时 {median(scenario_timing):.3f} 秒，最慢 {max(scenario_timing):.3f} 秒。"
                 "所有导出的计划均通过独立校验。", "",
                 "| 场景 | 候选数 | 交付优先准交率 | 交付优先调整任务 | 最小扰动调整任务 | 整批秒数 |",
                 "|---|---:|---:|---:|---:|---:|"])
    for item in result["scenarios"]:
        top = item["candidates"][0]
        stable = next((plan for plan in item["candidates"] if plan["strategy"] == "最小扰动"), None)
        stable_count = f"{stable['moved']} / {stable['comparable']}" if stable else "同其他候选"
        text.append(f"| {item['id']} | {len(item['candidates'])} | {top['otd']:.2%} |"
                    f" {top['moved']} / {top['comparable']} | {stable_count} | {item['timing']['solver_seconds']:.3f} |")
    text += ["", "## 验收依赖", "",
             "目前使用确定性规则基准。待补同一窗口的人工计划、可靠的班次与物料可用数据，才能复算企业历史收益。"
             "现有数据中的静态库存与安全库存用于生产净需求计算。停机、物料到货与完工记录在本实验中用于模拟。",
             "预期完工之前仅看得到当时已接订单。准交率分母包含未排订单。加权延期只统计有预计完工的订单，"
             "未排数量单列。负载 CV 以相同规划窗口内各可用产线的占用率计算。"]
    return "\n".join(text) + "\n"


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="回放完整数据及 20 组突发场景，导出实验材料")
    cli.add_argument("workbook", type=Path)
    cli.add_argument("output", type=Path)
    args = cli.parse_args()
    result = evaluate(args.workbook, args.output)
    print(f"{len(result['candidates'])} 个候选，{len(result['scenarios'])} 组场景，完整数据 10 次计时。")
