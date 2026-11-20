from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import BinaryIO

from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel


ZERO = Decimal(0)
EPOCH = datetime(2000, 1, 1)
PRIORITY_WEIGHT = {1: 3, 2: 2, 3: 1}


class InputError(ValueError):
    pass


def minute(value: datetime) -> int:
    return int((value - EPOCH) // timedelta(minutes=1))


def timestamp(value: int) -> str:
    return (EPOCH + timedelta(minutes=value)).isoformat(timespec="minutes")


def parse_timestamp(value: str) -> int:
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo or parsed.second or parsed.microsecond:
            raise ValueError
        return minute(parsed)
    except (TypeError, ValueError, OverflowError) as exc:
        raise InputError("日期应为北京时间，精确到分钟，例如 2026-04-27T08:00。") from exc


def family(model: str) -> str:
    return model.strip().removeprefix("轴承").split("-", 1)[0].strip().upper()


@dataclass(frozen=True, slots=True)
class Order:
    id: str
    customer: str
    code: str
    model: str
    qty: int
    received: int
    due: int
    priority: int

    @property
    def family(self) -> str:
        return family(self.model)


@dataclass(frozen=True, slots=True)
class Line:
    id: str
    families: frozenset[str]
    capacity: int
    major: int | None
    minor: int
    cycle: int

    def duration(self, qty: int) -> int:
        return (qty * 60 + self.capacity - 1) // self.capacity


@dataclass(frozen=True, slots=True)
class Issue:
    category: str
    sheet: str
    row: int
    reference: str
    detail: str


@dataclass(frozen=True, slots=True)
class Dataset:
    orders: tuple[Order, ...]
    inventory: dict[str, int]
    safety: dict[str, Decimal]
    lines: dict[str, Line]
    calendar: dict[str, tuple[tuple[int, int], ...]]
    issues: tuple[Issue, ...]


@dataclass(frozen=True, slots=True)
class Job:
    id: str
    code: str
    model: str
    qty: int
    due: int
    priority: int
    received: int
    release: int
    order_id: str | None = None

    @property
    def family(self) -> str:
        return family(self.model)


@dataclass(frozen=True, slots=True)
class Balance:
    code: str
    model: str
    demand: int
    inventory: int
    safety: Decimal
    net: Decimal
    allocated: int
    order_production: int
    replenishment: Decimal
    replenishment_units: int
    eligible: tuple[str, ...]
    inventory_known: bool
    safety_known: bool


@dataclass(frozen=True, slots=True)
class Demand:
    orders: tuple[Order, ...]
    allocated: dict[str, int]
    balances: tuple[Balance, ...]
    jobs: dict[str, Job]


def read_dataset(source: Path | BinaryIO) -> Dataset:
    try:
        workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise InputError("无法读取 Excel 工作簿，请使用有效的 .xlsx 文件。") from exc
    try:
        sheets = {sheet.title.strip(): sheet for sheet in workbook}
        return _read_sheets(sheets, workbook.epoch)
    finally:
        workbook.close()


def _read_sheets(sheets, epoch) -> Dataset:
    required = ("订单需求", "产品及库存信息", "安全库存标准", "产线信息")
    missing = set(required) - sheets.keys()
    if missing:
        raise InputError("缺少工作表：" + "、".join(sorted(missing)))
    expected_headers = (
        ("客户简称", "产品编码", "产品型号", "需求数量", "接单日期", "发货日期", "客户优先级"),
        ("产品编码", "产品型号", "库存数量"),
        ("产品编码", "产品型号", "安全库存"),
        ("线号", "可生产产品型号", "换型时间", None, "小时产能", "生产周期"),
    )
    for name, headers in zip(required, expected_headers):
        row = next(sheets[name].iter_rows(max_row=1, max_col=len(headers), values_only=True), ())
        if any(header and not _text(cell).startswith(header) for header, cell in zip(headers, row)):
            raise InputError(f"{name}：表头与新大地原表不符。")

    orders = []
    issues = []
    models = {}
    for row, (customer, code, model, qty, received, due, priority) in _rows(sheets[required[0]], 7):
        where = f"{required[0]}!第 {row} 行"
        code, model = _required(code, where), _required(model, where)
        prev_model = models.setdefault(code, model)
        if prev_model != model:
            raise InputError(f"{where}：同一产品编码 {code} 在订单中对应多个型号。")
        received = _excel_time(received, epoch, where)
        due = _excel_time(due, epoch, where, end_of_day=True)
        priority = _integer(priority, where, positive=True)
        if due < received or priority not in PRIORITY_WEIGHT:
            raise InputError(f"{where}：交期早于接单日期或客户优先级超出 1–3。")
        orders.append(Order(f"O{row:05d}", _text(customer), code, model,
                            _integer(qty, where, positive=True), received, due, priority))
    if not orders:
        raise InputError("订单需求没有有效数据行。")

    inventory, inventory_rows = _master(sheets[required[1]], integral=True, models=models, issues=issues)
    safety, safety_rows = _master(sheets[required[2]], integral=False, models=models, issues=issues)
    lines = {}
    for row, (code, families, major, minor, cap, cycle) in _rows(sheets[required[3]], 6, start=3):
        where = f"{required[3]}!第 {row} 行"
        code = _required(code, where)
        families = frozenset(_text(f).upper().strip() for f in _required(families, where).split("/") if f.strip())
        if code in lines:
            raise InputError(f"{where}：线号 {code} 重复。")
        if not families:
            raise InputError(f"{where}：产线缺少可生产型号。")
        if major is None and len(families) > 1:
            major = 2
            issues.append(Issue("大换型缺失", required[3], row, code, "多型号产线暂用 2 小时，待企业确认。"))
        lines[code] = Line(code, families, _integer(cap, where, positive=True),
                           None if major is None else _hours(major, where),
                           _hours(minor, where), _hours(cycle, where))
    if not lines:
        raise InputError("产线信息没有有效数据行。")

    first_order = {}
    for order in orders:
        first_order.setdefault(order.code, order)
    supported = set().union(*(line.families for line in lines.values()))
    for code, order in first_order.items():
        row = int(order.id[1:])
        if code not in inventory_rows:
            issues.append(Issue("缺库存", required[0], row, code, "原表缺库存记录，回放按 0。"))
        if code not in safety_rows:
            issues.append(Issue("缺安全库存", required[0], row, code, "原表缺安全库存记录，回放按 0。"))
        if order.family not in supported:
            issues.append(Issue("无适配线", required[0], row, code, f"型号族 {order.family} 无适配线，需人工确认。"))

    calendar = defaultdict(list)
    if sheet := sheets.get("产线日历"):
        for row, (code, start, end) in _rows(sheet, 3):
            where = f"产线日历!第 {row} 行"
            code = _required(code, where)
            start, end = _excel_time(start, epoch, where), _excel_time(end, epoch, where)
            if code not in lines or start >= end:
                raise InputError(f"{where}：线号无效或可用区间终点未晚于起点。")
            calendar[code].append((start, end))
    if not calendar:
        issues.append(Issue("缺产线日历", "产线信息", 0, "", "回放采用 24 小时连续可用；可选「产线日历」补充班次。"))
    return Dataset(tuple(orders), inventory, safety, lines,
                   {key: tuple(sorted(value)) for key, value in calendar.items()}, tuple(issues))


def _rows(sheet, count, start=2):
    for row, cells in enumerate(sheet.iter_rows(min_row=start, max_col=count, values_only=True), start):
        if any(value is not None and str(value).strip() for value in cells):
            yield row, cells


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _required(value, where) -> str:
    text = _text(value)
    if not text:
        raise InputError(f"{where}：必填字段为空。")
    return text


def _decimal(value, where) -> Decimal:
    try:
        qty = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise InputError(f"{where}：数量、产能或时长不是有效数值。") from exc
    if not qty.is_finite() or qty < 0:
        raise InputError(f"{where}：数量、产能或时长必须是非负有限数值。")
    return qty


def _integer(value, where, positive=False) -> int:
    qty = _decimal(value, where)
    if qty != qty.to_integral_value() or (positive and qty == 0):
        raise InputError(f"{where}：该字段需要{'正' if positive else '非负'}整数。")
    return int(qty)


def _hours(value, where) -> int:
    return int((_decimal(value, where) * 60).to_integral_value(rounding=ROUND_CEILING))


def _excel_time(value, epoch, where, end_of_day=False) -> int:
    try:
        if isinstance(value, (int, float)):
            value = from_excel(value, epoch)
        elif isinstance(value, str):
            value = datetime.fromisoformat(value.strip().replace("/", "-"))
        elif isinstance(value, date) and not isinstance(value, datetime):
            value = datetime.combine(value, datetime.min.time())
        if not isinstance(value, datetime) or value.tzinfo:
            raise ValueError
        if end_of_day and value.time() == datetime.min.time():
            value = value.replace(hour=23, minute=59)
        return minute(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise InputError(f"{where}：日期或 Excel 序列号无效。") from exc


def _master(sheet, *, integral, models, issues):
    values, rows = {}, {}
    for row, (code, model, qty) in _rows(sheet, 3):
        where = f"{sheet.title.strip()}!第 {row} 行"
        code, model = _required(code, where), _required(model, where)
        if code in values:
            raise InputError(f"{where}：产品编码 {code} 重复。")
        values[code] = _integer(qty, where) if integral else _decimal(qty, where)
        rows[code] = row
        if code in models and model != models[code]:
            issues.append(Issue("型号描述差异", sheet.title.strip(), row, code,
                                f"主表 {model}，订单 {models[code]}。库存按编码对账，线体按订单型号匹配。"))
    return values, rows


def order_key(order: Order):
    return order.priority, order.received, order.due, order.id


def build_demand(dataset: Dataset, orders: tuple[Order, ...], as_of: int, end: int,
                 fixed_stock: dict[str, int] | None = None,
                 material_release: dict[str, int] | None = None,
                 priority_overrides: dict[str, int] | None = None) -> Demand:
    fixed_stock = fixed_stock or {}
    material_release = material_release or {}
    priority_overrides = priority_overrides or {}
    by_code = defaultdict(list)
    for order in orders:
        by_code[order.code].append(order)
    balances, allocation, jobs = [], {}, {}
    for code in sorted(by_code):
        group = sorted(by_code[code], key=order_key)
        model = group[0].model
        original_stock = dataset.inventory.get(code, 0)
        remaining = original_stock - sum(fixed_stock.get(order.id, 0) for order in group)
        if remaining < 0:
            raise InputError(f"{code}：保留的库存分配超过原始可用库存。")
        total = sum(order.qty for order in group)
        safety = dataset.safety.get(code, ZERO)
        net = max(Decimal(total) + safety - original_stock, ZERO)
        production = 0
        release = max(as_of, material_release.get(code, as_of))
        for order in group:
            if order.id in fixed_stock:
                covered = fixed_stock[order.id]
            else:
                covered = min(remaining, order.qty)
                remaining -= covered
            allocation[order.id] = covered
            qty = order.qty - covered
            if qty:
                job_id = f"order:{order.id}"
                jobs[job_id] = Job(job_id, code, order.model, qty, order.due,
                                   priority_overrides.get(order.id, order.priority),
                                   order.received, max(release, order.received), order.id)
                production += qty
        replenish = net - production
        if replenish < 0:
            raise InputError(f"{code}：库存分配和订单生产量无法对账。")
        units = int(replenish.to_integral_value(rounding=ROUND_CEILING))
        if units:
            job_id = f"stock:{code}"
            jobs[job_id] = Job(job_id, code, model, units, end, 4, as_of, release)
        balances.append(Balance(code, model, total, original_stock, safety, net,
                                total - production, production, replenish, units,
                                tuple(line.id for line in dataset.lines.values() if family(model) in line.families),
                                code in dataset.inventory, code in dataset.safety))
    return Demand(orders, allocation, tuple(balances), jobs)


def data_summary(dataset: Dataset, demand: Demand) -> dict:
    balances = demand.balances
    return {
        "orders": len(demand.orders), "codes": len(balances), "lines": len(dataset.lines),
        "order_units": sum(row.demand for row in balances),
        "positive_codes": sum(row.net > 0 for row in balances),
        "net_units": str(sum((row.net for row in balances), ZERO)),
        "production_units": sum(job.qty for job in demand.jobs.values()),
        "unsupported_units_exact": str(sum((row.net for row in balances if not row.eligible), ZERO)),
        "issue_counts": dict(Counter(issue.category for issue in dataset.issues)),
        "first_order": timestamp(min(order.received for order in demand.orders)),
        "last_order": timestamp(max(order.received for order in demand.orders)),
        "last_due": timestamp(max(order.due for order in demand.orders)),
    }
