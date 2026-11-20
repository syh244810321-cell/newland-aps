import argparse
import getpass
import logging
import os
from threading import Event

from .api import ROOT, database_url
from .service import ROLES, Service, WorkflowError


def main():
    parser = argparse.ArgumentParser(description="新大地 APS 企业工作台")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("serve")
    start.add_argument("--port", type=int, default=8010)
    start.add_argument("--demo", action="store_true")
    start.add_argument("--source")
    commands.add_parser("worker")
    new = commands.add_parser("create-user")
    new.add_argument("username")
    new.add_argument("name")
    new.add_argument("role", choices=ROLES)
    new.add_argument("--plant", default="newland")
    new.add_argument("--plant-name", default="新大地 · 生产工厂")
    args = parser.parse_args()
    (ROOT / ".var").mkdir(exist_ok=True)
    if args.command == "serve":
        import uvicorn

        if args.demo:
            os.environ["APS_PRODUCT_DEMO"] = "1"
        if args.source:
            os.environ["APS_SAMPLE_XLSX"] = args.source
        uvicorn.run("aps.product.api:from_env", factory=True, host="127.0.0.1", port=args.port)
        return
    service = Service(database_url())
    if args.command == "worker":
        logging.basicConfig(level=logging.INFO)
        try:
            service.worker(Event())
        except KeyboardInterrupt:
            return
    else:
        if os.environ.get("APS_AUTO_SCHEMA", "1") == "1":
            service.initialize()
        password = getpass.getpass("密码（至少 12 位）: ")
        if len(password) < 12 or password != getpass.getpass("重复密码: "):
            parser.exit(2, "密码不一致或长度不足。\n")
        service.seed_plant(args.plant, args.plant_name)
        try:
            service.add_user(args.plant, args.username, args.name, args.role, password)
        except WorkflowError as exc:
            parser.exit(2, f"{exc}\n")


if __name__ == "__main__":
    main()
