"""为 AI 提供低 Token 的知识库选址路由与候选查询。"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .knowledge_structure import write_generated_snapshot

ROUTING_SNAPSHOT_FILENAME = "KNOWLEDGE_ROUTING.md"


def _one_line(value: Any, limit: int = 120) -> str:
    safe = "".join(
        " " if character.isspace() else character
        for character in str(value or "")
        if unicodedata.category(character) != "Cc" or character.isspace()
    )
    text = " ".join(safe.split()).replace("`", "ˋ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _is_reusable_route(folder: dict[str, Any]) -> bool:
    """优先遵守显式角色，再用保守启发式区分类别与具体资料叶子。"""
    role = str(folder.get("placement_role") or "auto").strip().casefold()
    if role == "route":
        return True
    if role == "leaf":
        return False
    if folder.get("child_count"):
        return True
    files = list(folder.get("files") or [])
    if folder.get("primary_file") is not None or files:
        return False
    return True


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(r"[^0-9a-z_+#\u3400-\u9fff]+", " ", text).split())


def _query_terms(title: str, keywords: str) -> list[str]:
    terms: list[str] = []
    for source in (keywords, title):
        for term in _normalize(source).split():
            if len(term) < 2 or term in terms:
                continue
            terms.append(term)
    return terms


def _folder_text(folder: dict[str, Any], key: str) -> str:
    return _normalize(folder.get(key))


def _score_route(folder: dict[str, Any], terms: list[str], direct_children: list[str]) -> tuple[int, list[str]]:
    name = _folder_text(folder, "name")
    title = _folder_text(folder, "title")
    path = _folder_text(folder, "path")
    summary = _folder_text(folder, "summary")
    tags = _normalize(" ".join(str(tag) for tag in folder.get("tags") or []))
    child_text = _normalize(" ".join(direct_children))
    matched: list[str] = []
    score = 0
    for term in terms:
        weights = [
            24 if term in {name, title} else 0,
            18 if term in name or term in title else 0,
            13 if term in path else 0,
            12 if term in tags else 0,
            10 if term in summary else 0,
            8 if term in child_text else 0,
        ]
        weight = max(weights)
        if weight:
            score += weight
            matched.append(term)
    if matched:
        score += min(int(folder.get("depth") or 0), 8)
    return score, matched


def _possible_duplicates(folders: list[dict[str, Any]], title: str) -> list[dict[str, str]]:
    needle = _normalize(title)
    if not needle:
        return []
    allow_contains = len(needle) >= 3
    duplicates: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for folder in folders:
        path = str(folder.get("path") or "")
        for label in {str(folder.get("name") or ""), str(folder.get("title") or "")}:
            normalized = _normalize(label)
            if normalized and (
                needle == normalized
                or (allow_contains and (needle in normalized or normalized in needle))
            ):
                key = ("folder", path, label)
                if key not in seen:
                    seen.add(key)
                    duplicates.append({"kind": "folder", "path": path, "name": label})
        for file in folder.get("files") or []:
            name = str(file.get("name") or "")
            stem = Path(name).stem
            normalized = _normalize(stem)
            if normalized and (
                needle == normalized
                or (allow_contains and (needle in normalized or normalized in needle))
            ):
                relative_path = str(file.get("relative_path") or f"{path}/{name}".lstrip("/"))
                key = ("file", relative_path, name)
                if key not in seen:
                    seen.add(key)
                    duplicates.append({"kind": "file", "path": relative_path, "name": name})
    duplicates.sort(key=lambda item: (item["path"].casefold(), item["kind"], item["name"].casefold()))
    return duplicates[:10]


def query_placement(
    catalog: dict[str, Any],
    *,
    title: str = "",
    keywords: str = "",
    limit: int = 3,
) -> dict[str, Any]:
    """从 catalog 返回小型、可解释且稳定排序的资料放置候选。"""
    title = str(title).strip()
    keywords = str(keywords).strip()
    if not title and not keywords:
        raise ValueError("title 和 keywords 至少提供一个")
    if len(title) + len(keywords) > 1000:
        raise ValueError("查询内容过长")
    if not 1 <= int(limit) <= 5:
        raise ValueError("limit 必须在 1 到 5 之间")

    folders = list(catalog.get("folders") or [])
    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for folder in folders:
        children_by_parent.setdefault(str(folder.get("parent_path") or ""), []).append(folder)
    for children in children_by_parent.values():
        children.sort(key=lambda child: str(child.get("title") or child.get("name") or "").casefold())

    terms = _query_terms(title, keywords)
    scored: list[tuple[int, str, dict[str, Any], list[str], list[str]]] = []
    for folder in folders:
        if not _is_reusable_route(folder):
            continue
        direct_children = [str(child.get("name") or child.get("title") or "") for child in children_by_parent.get(str(folder.get("path") or ""), [])]
        score, matched = _score_route(folder, terms, direct_children)
        if score:
            scored.append((score, str(folder.get("path") or "").casefold(), folder, matched, direct_children))

    scored.sort(key=lambda item: (-item[0], -int(item[2].get("depth") or 0), item[1]))
    selected = scored[:limit]
    if not selected:
        fallback = [folder for folder in folders if _is_reusable_route(folder) and int(folder.get("depth") or 0) == 1]
        fallback.sort(key=lambda folder: str(folder.get("path") or "").casefold())
        selected = [
            (0, str(folder.get("path") or "").casefold(), folder, [], [
                str(child.get("name") or child.get("title") or "")
                for child in children_by_parent.get(str(folder.get("path") or ""), [])
            ])
            for folder in fallback[:limit]
        ]

    candidates = [
        {
            "path": str(folder.get("path") or ""),
            "title": str(folder.get("title") or folder.get("name") or ""),
            "summary": _one_line(folder.get("summary")),
            "score": score,
            "matched_terms": matched,
            "direct_children": direct_children[:12],
        }
        for score, _sort_path, folder, matched, direct_children in selected
    ]
    top_score = candidates[0]["score"] if candidates else 0
    matched_count = len(candidates[0]["matched_terms"]) if candidates else 0
    confidence = "high" if top_score >= 35 and matched_count >= 2 else "medium" if top_score >= 15 else "low"
    return {
        "revision": str(catalog.get("revision") or ""),
        "query": {"title": title, "keywords": keywords, "terms": terms},
        "confidence": confidence,
        "candidates": candidates,
        "possible_duplicates": _possible_duplicates(folders, title),
    }


def render_routing_snapshot(catalog: dict[str, Any]) -> str:
    """渲染只含可复用目录的精简 AI 选址路由。"""
    folders = [folder for folder in catalog.get("folders") or [] if _is_reusable_route(folder)]
    folders.sort(key=lambda folder: (int(folder.get("depth") or 0), str(folder.get("path") or "").casefold()))
    lines = [
        "# 知识库 AI 选址路由（自动生成）",
        "",
        "> 先用 `query_placement.py` 获取候选；仅在候选不足时阅读本文件。这里不列具体资料附件，也不替代目标目录的局部冲突检查。",
        "",
        f"- 结构修订：`{catalog.get('revision', '')}`",
        f"- 可复用目录：{len(folders)}",
        "",
        "## 可复用目录",
        "",
    ]
    if not folders:
        lines.append("- （暂无可复用目录）")
    for folder in folders:
        path = _one_line(folder.get("path"), 240) or "未命名"
        summary = _one_line(folder.get("summary"))
        detail = f" — {summary}" if summary else ""
        lines.append(f"- `{path}`{detail}")
    lines.append("")
    return "\n".join(lines)


def write_routing_snapshot(path: Path, catalog: dict[str, Any]) -> bool:
    """渲染并原子更新固定的精简 AI 路由快照。"""
    return write_generated_snapshot(path, render_routing_snapshot(catalog))
