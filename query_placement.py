#!/usr/bin/env python3
"""查询资料应放置的候选目录，只输出 AI 选址所需的最小结果。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.kb_scanner import load_json_safe, scan_library
from src.placement_router import query_placement

PROJECT_DIR = Path(__file__).resolve().parent


def render_text(result: dict[str, Any]) -> str:
    lines = [
        f"结构修订：{result['revision']}",
        f"置信度：{result['confidence']}",
        "候选目录：",
    ]
    candidates = list(result.get("candidates") or [])
    if not candidates:
        lines.append("- 无；请读取 KNOWLEDGE_ROUTING.md 后再决定是否扩大检查范围。")
    for index, candidate in enumerate(candidates, 1):
        matched = "、".join(candidate.get("matched_terms") or []) or "无直接词项"
        children = "、".join(candidate.get("direct_children") or []) or "无"
        lines.extend([
            f"{index}. {candidate['path']}（分数 {candidate['score']}；命中：{matched}）",
            f"   摘要：{candidate['summary']}",
            f"   直属子目录：{children}",
        ])
    duplicates = list(result.get("possible_duplicates") or [])
    lines.append("可能重复：")
    if not duplicates:
        lines.append("- 未发现标题近似项；仍需检查最终目标目录的直属同名冲突。")
    else:
        for duplicate in duplicates:
            lines.append(f"- {duplicate['kind']}: {duplicate['path']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="返回最多 5 个精简的知识库资料放置候选")
    parser.add_argument("--base-dir", type=Path, default=PROJECT_DIR, help="项目根目录")
    parser.add_argument("--title", default="", help="资料或题目标题")
    parser.add_argument("--keywords", default="", help="以空格分隔的知识点、模型和操作关键词")
    parser.add_argument("--limit", type=int, default=3, help="候选数量，1 到 5")
    parser.add_argument("--json", action="store_true", help="输出精简 JSON")
    args = parser.parse_args()

    base_dir = args.base_dir.expanduser().resolve()
    library_dir = base_dir / "library"
    if not library_dir.is_dir():
        parser.error(f"知识库目录不存在：{library_dir}")
    catalog = scan_library(library_dir, load_json_safe(base_dir / "config.json", {}))
    try:
        result = query_placement(
            catalog,
            title=args.title,
            keywords=args.keywords,
            limit=args.limit,
        )
    except ValueError as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
