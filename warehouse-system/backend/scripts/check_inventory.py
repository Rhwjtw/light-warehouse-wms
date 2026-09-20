"""Read-only V1 stock/history reconciliation; nonzero exit on discrepancy."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy.orm import Session
from app.database import engine
from app.reconciliation import reconcile


def main():
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        with connection.begin(), Session(bind=connection) as session:
            result = reconcile(session)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
