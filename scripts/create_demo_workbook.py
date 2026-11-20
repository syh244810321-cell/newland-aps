"""Create a fully synthetic workbook for public demos; never reads company data."""
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]


def create_demo(path: Path) -> None:
    book = Workbook()
    orders = book.active
    orders.title = "订单需求"
    orders.append(["客户简称", "产品编码", "产品型号", "需求数量", "接单日期", "发货日期", "客户优先级"])
    start = datetime(2026, 11, 1)
    products = [
        ("DEMO-6201", "6201-2RS", 120, 80),
        ("DEMO-6202", "6202-2RS", 60, 50),
        ("DEMO-6203", "6203-2RS", 90, 40),
    ]
    for index in range(12):
        code, model, _, _ = products[index % len(products)]
        orders.append([
            f"演示客户 {chr(65 + index % 3)}", code, model, 600 + index * 75,
            start, start + timedelta(days=2 + index // 3), 1 + index % 3,
        ])
    orders.append(["演示客户 X", "DEMO-UNSUPPORTED", "DEMO-X", 50, start, start + timedelta(days=4), 2])
    stock = book.create_sheet("产品及库存信息")
    stock.append(["产品编码", "产品型号", "库存数量"])
    safety = book.create_sheet("安全库存标准")
    safety.append(["产品编码", "产品型号", "安全库存"])
    for code, model, inventory, minimum in products:
        stock.append([code, model, inventory])
        safety.append([code, model, minimum])
    stock.append(["DEMO-UNSUPPORTED", "DEMO-X", 0])
    safety.append(["DEMO-UNSUPPORTED", "DEMO-X", 0])
    lines = book.create_sheet("产线信息")
    lines.append(["线号", "可生产产品型号", "换型时间", None, "小时产能", "生产周期"])
    lines.append([None, None, "大换型", "小换型", None, None])
    lines.append(["DEMO-L01", "6201/6202", 2, 0.25, 300, 4])
    lines.append(["DEMO-L02", "6202/6203", 1.5, 0.25, 260, 4])
    lines.append(["DEMO-L03", "6201/6203", 2, 0.5, 220, 4])
    calendar = book.create_sheet("产线日历")
    calendar.append(["线号", "可用开始", "可用结束"])
    for line in ("DEMO-L01", "DEMO-L02", "DEMO-L03"):
        calendar.append([line, start, start + timedelta(days=14)])
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    book.close()


if __name__ == "__main__":
    target = ROOT / ".var" / "demo.xlsx"
    create_demo(target)
    print("Synthetic demo workbook created: .var/demo.xlsx")
