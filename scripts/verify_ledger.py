"""Re-walk the ledger's hash chain. Exit 0 if intact, 1 if anything was changed, 2 if it cannot be read.

    python scripts/verify_ledger.py [--db PATH]

Without --db it reads the path from config/runtime.yaml.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from satyasutra import __version__  # noqa: E402
from satyasutra.config import load_config  # noqa: E402
from satyasutra.errors import ConfigError  # noqa: E402
from satyasutra.ledger import SQLiteLedgerStore  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="path to the ledger database (default: from config/runtime.yaml)")
    args = parser.parse_args(argv)
    try:
        db = Path(args.db) if args.db else ROOT / load_config().runtime.ledger_db_path
    except ConfigError as exc:
        print(f"cannot load config: {exc}", file=sys.stderr)
        return 2
    if not db.is_file():
        print(f"no ledger at {db}", file=sys.stderr)
        return 2
    try:
        result = SQLiteLedgerStore(db, code_version=__version__).verify()
    except sqlite3.DatabaseError as exc:
        print(f"cannot read ledger: {exc}", file=sys.stderr)
        return 2
    if result.ok:
        print(f"OK: {result.checked} entries, chain intact")
        return 0
    print(f"TAMPERED at seq {result.first_bad_seq}: {result.reason} ({result.checked} entries verified before it)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
