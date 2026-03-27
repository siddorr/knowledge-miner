#!/usr/bin/env python3
from __future__ import annotations

from knowledge_miner.config import (
    classify_database_target,
    database_target_warning,
    expected_database_target_for_role,
    settings,
)
from knowledge_miner.db import sqlite_file_metadata


def main() -> int:
    metadata = sqlite_file_metadata(settings.database_url)
    print(f"database_url={settings.database_url}")
    print(f"resolved_path={metadata.get('path')}")
    print(f"db_target_kind={classify_database_target(settings.database_url)}")
    print(f"expected_for_role={expected_database_target_for_role()}")
    print(f"warning={database_target_warning(settings.database_url) or '-'}")
    print(f"inode={metadata.get('inode')}")
    print(f"mtime={metadata.get('mtime')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
