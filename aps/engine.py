from collections import defaultdict
from dataclasses import replace
from random import Random
from time import perf_counter

from .data import InputError
from .schedule import Batch, Plan, Problem, measures, missing_quantities, placed_quantities, setup_minutes, windows
from .validator import violations


RULE = "规则基准"
STRATEGIES = {
    "交付优先": (0.70, 0.10, 0.10, 0.10),
    "换型优先": (0.30, 0.50, 0.10, 0.10),
    "负载均衡": (0.35, 0.10, 0.45, 0.10),
    "客户优先": (0.60, 0.15, 0.15, 0.10),
    "综合均衡": (0.45, 0.25, 0.20, 0.10),
}


class Scheduler:
    def __init__(self, problem: Problem):
        self.problem = problem
        self.jobs = problem.demand.jobs
        self.pins = defaultdict(list)
        for batch in sorted(problem.pins, key=lambda row: row.start):
            self.pins[batch.line].append(batch)
        self.capacity = {key: tuple(windows(problem, key)) for key in problem.dataset.lines}
        fixed = placed_quantities(problem.pins)
        self.pending = {job.id: job.qty - fixed[job.id] for job in self.jobs.values()
                        if job.qty > fixed[job.id]}
        self.eligible = {
            job_id: tuple(line for line in problem.dataset.lines.values() if self.jobs[job_id].family in line.families)
            for job_id in self.pending
        }
        self.locked_lines = {batch.line for batch in problem.pins if batch.status == 'locked'}
        self.previous_slots = {(old.job, old.line) for old in problem.previous}
        self.budget = max(1, problem.end - problem.at)

    def fit(self, line, job, earlier):
        qty = self.pending[job.id]
        runtime = line.duration(qty)
        setup = setup_minutes(line, self.jobs[earlier.job] if earlier else None, job)
        first = max(self.problem.at, earlier.end if earlier else self.problem.at, job.release - setup)
        for start, end in self.capacity[line.id]:
            setup_start = max(first, start)
            finish = setup_start + setup + runtime
            if finish <= end:
                return Batch(job.id, line.id, qty, setup_start, setup_start + setup, finish)
        return None

    def available_slot(self, line, job, rows):
        positions = range(len(rows) + 1) if line.id in self.locked_lines else (len(rows),)
        for index in positions:
            batch = self.fit(line, job, rows[index - 1] if index else None)
            if batch is None:
                break
            if index < len(rows):
                later = rows[index]
                required = setup_minutes(line, job, self.jobs[later.job])
                if batch.end > later.setup or later.start - later.setup < required:
                    continue
            return batch, index
        return None

    def construct(self, strategy):
        def priority(job):
            return job.priority, job.received, job.due, job.id

        def key(job):
            stock = job.order_id is None
            if strategy == "交付优先":
                return stock, job.due, *priority(job)
            if strategy == "换型优先":
                return stock, job.family, job.code, *priority(job)
            if strategy == "负载均衡":
                return stock, len(self.eligible[job.id]), -self.pending[job.id], *priority(job)
            if strategy == "综合均衡":
                return stock, len(self.eligible[job.id]), job.due, *priority(job)
            return stock, *priority(job)

        rows = {line: list(self.pins[line]) for line in self.capacity}
        loads = {line: sum(batch.end - batch.setup for batch in batches) for line, batches in rows.items()}
        for job in sorted((self.jobs[key] for key in self.pending), key=key):
            options = []
            for line in self.eligible[job.id]:
                current = rows[line.id]
                slot = self.available_slot(line, job, current)
                if slot is None:
                    continue
                batch, index = slot
                setup = batch.start - batch.setup
                late = max(0, batch.end - job.due)
                same_line = (job.id, line.id) in self.previous_slots
                if strategy == "换型优先":
                    rank = (late, setup, batch.end)
                elif strategy == "负载均衡":
                    rank = (late, loads[line.id] + batch.end - batch.setup, batch.end)
                elif strategy in ("综合均衡", "客户优先"):
                    rank = (late, 0 if same_line else 1, batch.end + setup, loads[line.id])
                else:
                    rank = (batch.end, setup, loads[line.id])
                options.append((rank, line.id, index, batch))
            if options:
                _, line, index, batch = min(options)
                rows[line].insert(index, batch)
                loads[line] += batch.end - batch.setup
        return rows

    def stable(self):
        if self.locked_lines:
            return self.construct('客户优先')
        rows = {line: list(self.pins[line]) for line in self.capacity}
        pending = set(self.pending)
        for old in sorted(self.problem.previous, key=lambda b: (b.line, b.setup)):
            if old.job not in pending or old.qty != self.pending[old.job] or old.setup < self.problem.at:
                continue
            job = self.jobs[old.job]
            if old.start < job.release or not any(a <= old.setup and old.end <= b for a, b in self.capacity[old.line]):
                continue
            line = self.problem.dataset.lines[old.line]
            current = rows[old.line]
            previous = current[-1] if current else None
            if previous and previous.end > old.setup:
                continue
            setup = setup_minutes(line, self.jobs[previous.job] if previous else None, job)
            if old.start - old.setup >= setup:
                current.append(replace(old, status="planned"))
                pending.remove(old.job)

        for job in sorted((self.jobs[key] for key in pending),
                          key=lambda j: (j.order_id is None, j.priority, j.due, j.received, j.id)):
            options = []
            for line in self.eligible[job.id]:
                current = rows[line.id]
                for i in range(len(self.pins[line.id]), len(current) + 1):
                    previous = current[i - 1] if i else None
                    candidate = self.fit(line, job, previous)
                    if candidate is None:
                        break
                    next_batch = current[i] if i < len(current) else None
                    if next_batch:
                        required = setup_minutes(line, job, self.jobs[next_batch.job])
                        if candidate.end > next_batch.setup or next_batch.start - next_batch.setup < required:
                            continue
                    rank = (max(0, candidate.end - job.due), candidate.end, candidate.start - candidate.setup,
                            line.id, i)
                    options.append((rank, candidate))
                    break
            if options:
                rank, result = min(options)
                rows[result.line].insert(rank[-1], result)
        return rows

    def score(self, rows, strategy):
        batches = tuple(batch for line in rows.values() for batch in line)
        m = measures(self.problem, batches, missing_quantities(self.problem, batches))
        late, setup, balance, duration = STRATEGIES[strategy]
        horizon = self.budget / 60
        value = (late * ((1 - m["otd"]) + m["weighted_tardy_hours"] / (horizon * m["orders"]))
                 + setup * m["setup_hours"] / horizon + balance * m["load_cv"]
                 + duration * m["makespan_hours"] / horizon)
        if m["comparable"]:
            value += 0.15 * m["moved"] / m["comparable"] + 0.05 * m["shift_hours"] / (horizon * m["comparable"])
        return m["pending_weight"], m["missing_order_units"], m["shortfall_units"], value

    def improve(self, rows, strategy, deadline):
        if self.locked_lines:
            return rows
        random = Random(strategy)
        scored = self.score(rows, strategy)
        active = tuple(line for line, batches in rows.items() if len(batches) > len(self.pins[line]))
        for _ in range(32):
            if not active or perf_counter() >= deadline:
                break
            line = random.choice(active)
            first = len(self.pins[line])
            if first == len(rows[line]):
                continue
            index = random.randrange(first, len(rows[line]))
            job = rows[line][index].job
            options = self.eligible[job]
            other = random.choice(options).id
            if line == other:
                if index + 1 == len(rows[line]):
                    continue
                sequence = [batch.job for batch in rows[line][first:]]
                pos = index - first
                sequence[pos], sequence[pos + 1] = sequence[pos + 1], sequence[pos]
                orders = {line: sequence}
            else:
                orders = {line: [batch.job for batch in rows[line][first:] if batch.job != job],
                          other: [batch.job for batch in rows[other][len(self.pins[other]):]]}
                orders[other].insert(random.randrange(len(orders[other]) + 1), job)
            proposal = dict(rows)
            for key, sequence in orders.items():
                arranged = list(self.pins[key])
                for job_id in sequence:
                    batch = self.fit(self.problem.dataset.lines[key], self.jobs[job_id],
                                     arranged[-1] if arranged else None)
                    if batch is None:
                        break
                    arranged.append(batch)
                else:
                    proposal[key] = arranged
                    continue
                break
            else:
                candidate = self.score(proposal, strategy)
                if candidate < scored:
                    rows, scored = proposal, candidate
        return rows

    def assemble(self, rows, name):
        batches = tuple(batch for line in rows.values() for batch in line)
        result = Plan(name, batches, missing_quantities(self.problem, batches))
        if errors := violations(self.problem, result):
            raise InputError("独立校验未通过：" + "；".join(errors[:5]))
        return result


def solve(problem: Problem, *, seconds=1.25):
    begun = perf_counter()
    engine = Scheduler(problem)
    baseline = engine.assemble(engine.construct(RULE), RULE)
    first_feasible = perf_counter() - begun
    candidates = []
    fingerprints = set()
    strategies = (*tuple(STRATEGIES)[:4], "最小扰动") if problem.previous else STRATEGIES
    for strategy in strategies:
        if strategy == "最小扰动":
            rows = engine.stable()
        else:
            rows = engine.construct(strategy)
            deadline = min(begun + seconds, perf_counter() + seconds / len(strategies))
            rows = engine.improve(rows, strategy, deadline)
        plan = engine.assemble(rows, strategy)
        fingerprint = frozenset((r.job, r.line, r.start, r.qty) for r in plan.batches)
        if fingerprint not in fingerprints:
            fingerprints.add(fingerprint)
            candidates.append(plan)
    return baseline, candidates, {"solver_seconds": perf_counter() - begun,
                                  "first_feasible_seconds": first_feasible}
