#!/usr/bin/env python3
"""不启动网页服务，手动刷新知识库全局结构快照。"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.kb_scanner import load_json_safe, scan_library
from src.knowledge_structure import SNAPSHOT_FILENAME, write_structure_snapshot
from src.placement_router import ROUTING_SNAPSHOT_FILENAME, write_routing_snapshot

PROJECT_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="生成固定的完整结构快照与精简 AI 选址路由")
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=PROJECT_DIR,
        help="项目根目录，默认是脚本所在目录",
    )
    args = parser.parse_args()

    base_dir = args.base_dir.expanduser().resolve()
    catalog = scan_library(base_dir / "library", load_json_safe(base_dir / "config.json", {}))
    outputs = [
        (base_dir / SNAPSHOT_FILENAME, write_structure_snapshot),
        (base_dir / ROUTING_SNAPSHOT_FILENAME, write_routing_snapshot),
    ]
    for snapshot, writer in outputs:
        changed = writer(snapshot, catalog)
        state = "已更新" if changed else "无需改写"
        print(f"{state}：{snapshot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
