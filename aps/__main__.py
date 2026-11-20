import argparse
import json
from pathlib import Path

from .data import InputError, build_demand, data_summary, read_dataset
from .xlsx import audit_xlsx


def main():
    parser = argparse.ArgumentParser(description="新大地生产自动排程")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit", help="生成逐编码净需求对账、订单分配和异常清单")
    audit.add_argument("workbook", type=Path)
    audit.add_argument("output", type=Path)
    demo = commands.add_parser("serve", help="启动本地网页")
    demo.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn

        uvicorn.run("aps.api:app", host="127.0.0.1", port=args.port)
        return
    try:
        dataset = read_dataset(args.workbook)
        demand = build_demand(dataset, dataset.orders, min(o.received for o in dataset.orders),
                              max(o.due for o in dataset.orders))
    except InputError as exc:
        parser.exit(2, f"{exc}\n")
    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "净需求对账.xlsx"
    path.write_bytes(audit_xlsx(dataset, demand))
    print(json.dumps(data_summary(dataset, demand), ensure_ascii=False, indent=2))
    print(path.resolve())


if __name__ == "__main__":
    main()
