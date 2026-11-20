from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .data import data_summary, timestamp


def add_table(book, title, columns, rows, widths):
    sheet = book.create_sheet(title)
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = "s"
    for cell in sheet[1]:
        cell.font = Font(name="微软雅黑", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="173D5A")
        cell.alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 25
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    return sheet


def _book():
    book = Workbook()
    book.remove(book.active)
    return book


def _bytes(book):
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()


def audit_xlsx(dataset, demand):
    book = _book()
    totals = data_summary(dataset, demand)
    keys = (
        ("orders", "订单行"), ("codes", "订单产品编码"), ("lines", "有效产线"),
        ("order_units", "订单件数"), ("positive_codes", "正净需求编码"),
        ("net_units", "取整前正净需求精确值"), ("production_units", "取整后生产件数"),
        ("unsupported_units_exact", "无适配线净需求精确值")
    )
    add_table(book, "汇总", ("指标", "数值"), [(label, totals[key]) for key, label in keys]
              + list(totals["issue_counts"].items()), (35, 35))
    balances = add_table(
        book, "净需求对账",
        ("产品编码", "订单型号", "订单需求", "库存", "安全库存", "正净需求", "订单库存覆盖",
         "订单生产", "补库原值", "补库执行量", "取整差额", "适配产线", "库存记录", "安全库存记录"),
        ((r.code, r.model, r.demand, r.inventory, float(r.safety), float(r.net), r.allocated,
          r.order_production, float(r.replenishment), r.replenishment_units,
          float(r.replenishment_units - r.replenishment), " / ".join(r.eligible) or "无适配线",
          "有" if r.inventory_known else "缺失", "有" if r.safety_known else "缺失")
         for r in demand.balances),
        (17, 25, 13, 13, 16, 16, 16, 13, 16, 15, 14, 48, 14, 17),
    )
    for row in balances.iter_rows(min_row=2):
        for index in (4, 5, 8, 10):
            row[index].number_format = "#,##0.####"
    add_table(book, "订单分配", ("订单编号", "产品编码", "订单量", "库存覆盖", "订单生产", "接单", "交期", "优先级"),
              ((o.id, o.code, o.qty, demand.allocated[o.id], o.qty - demand.allocated[o.id],
                timestamp(o.received), timestamp(o.due), o.priority) for o in demand.orders),
              (15, 18, 13, 13, 13, 24, 24, 12))
    issues = add_table(book, "数据异常", ("类型", "源工作表", "Excel 行", "编号", "说明"),
                       ((i.category, i.sheet, i.row or None, i.reference, i.detail) for i in dataset.issues),
                       (18, 20, 12, 18, 82))
    for row in issues.iter_rows(min_row=2):
        row[-1].alignment = Alignment(wrap_text=True, vertical="center")
        issues.row_dimensions[row[-1].row].height = 30
    add_table(book, "产线参数", ("线号", "适配型号族", "小时产能", "大换型分钟", "小换型分钟", "生产周期分钟"),
              ((line.id, " / ".join(sorted(line.families)), line.capacity, line.major, line.minor, line.cycle)
               for line in dataset.lines.values()), (14, 42, 14, 18, 18, 18))
    rules = ((line.id, before, after, line.minor if before == after else line.major)
             for line in dataset.lines.values() for before in sorted(line.families) for after in sorted(line.families))
    add_table(book, "型号族换型", ("线号", "原型号族", "后型号族", "不同编码换型分钟"),
              rules, (14, 17, 17, 24))
    return _bytes(book)


def plan_xlsx(plan):
    book = _book()
    batches = plan["batches"]
    add_table(book, "作业计划", ("产线", "任务", "订单", "产品编码", "型号", "生产件数",
                              "计划开工", "计划完工", "状态", "换型开始"),
              ((r["line"], r["job"], r["order_id"], r["code"], r["model"], r["qty"], r["start"],
                r["end"], r["status"], r["setup_start"]) for r in batches),
              (12, 27, 14, 17, 26, 13, 23, 23, 16, 23))
    add_table(book, "订单交付", ("订单", "客户", "产品编码", "订单件数", "库存覆盖", "已安排", "交期",
                              "预计完工", "延期小时", "状态"),
              ((r["id"], r["customer"], r["code"], r["qty"], r["stock"], r["planned"], r["due"],
                r["completion"], r["tardy_hours"], r["status"]) for r in plan["orders"]),
              (14, 17, 17, 13, 13, 13, 23, 23, 15, 16))
    add_table(book, "换型", ("产线", "任务", "产品编码", "开始", "结束", "分钟"),
              ((r["line"], r["job"], r["code"], r["setup_start"], r["start"], r["setup_minutes"])
               for r in batches if r["setup_minutes"]),
              (12, 27, 17, 23, 23, 12))
    add_table(book, "异常", ("任务", "订单", "产品编码", "型号", "未排件数", "原因"),
              ((r["job"], r["order_id"], r["code"], r["model"], r["qty"], r["reason"])
               for r in plan["unassigned"]),
              (27, 15, 17, 26, 14, 40))
    return _bytes(book)
