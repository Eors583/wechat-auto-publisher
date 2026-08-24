from __future__ import annotations

import json

from app.config import database_target, load_config
from app.db import Database
from app.db_audit import audit_database


def main() -> int:
    report = audit_database(Database(database_target(load_config())))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
