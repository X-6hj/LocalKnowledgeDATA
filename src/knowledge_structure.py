"""生成供 AI 与人快速阅读的知识库全局结构快照。"""
from __future__ import annotations

import os
import tempfile
import unicodedata
from pathlib import Path
from typing import Any

SNAPSHOT_FILENAME = "KNOWLEDGE_STRUCTURE.md"


def _one_line(value: Any, limit: int = 96) -> str:
    safe = "".join(
        " " if character.isspace() else character
        for character in str(value or "")
        if unicodedata.category(character) != "Cc" or character.isspace()
    )
    text = " ".join(safe.split()).replace("`", "ˋ")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def render_structure_snapshot(catalog: dict[str, Any]) -> str:
    """把 catalog 的扁平目录节点渲染为确定性的完整 Markdown 树。"""
    folders = list(catalog.get("folders") or [])
    children: dict[str, list[dict[str, Any]]] = {}
    for folder in folders:
        children.setdefault(str(folder.get("parent_path") or ""), []).append(folder)

    lines = [
        "# 知识库全局结构（自动生成）",
        "",
        "> 这是 `library/` 的固定路径快照，供 AI 选址和人工总览使用。请勿手工编辑；启动项目或运行生成脚本时会按需覆盖同一个文件。",
        "",
        f"- 结构修订：`{catalog.get('revision', '')}`",
        f"- 目录数量：{catalog.get('stats', {}).get('folders', len(folders))}",
        f"- 文件数量：{catalog.get('stats', {}).get('files', 0)}",
        f"- 最大深度：{catalog.get('stats', {}).get('max_depth', 0)}",
        "- 说明：元数据文件（如 `_说明.md`、`_分类.md`）不作为普通资料列出；目录行已带上其摘要。",
        "",
        "## 完整目录树",
        "",
        "```text",
        "library/",
    ]

    def append_folder(folder: dict[str, Any], prefix: str, is_last: bool) -> None:
        branch = "└─ " if is_last else "├─ "
        summary = _one_line(folder.get("summary"))
        real_name = _one_line(folder.get("name") or folder.get("title") or "未命名", 160)
        display_title = _one_line(folder.get("title"), 160)
        folder_label = real_name
        if display_title and display_title != real_name:
            folder_label += f"（显示：{display_title}）"
        counts = f"{folder.get('child_count', 0)} 子目录 · {len(folder.get('files') or [])} 直属文件"
        detail = f" — {summary}" if summary else ""
        lines.append(f"{prefix}{branch}{folder_label}/  [{counts}]{detail}")

        child_prefix = prefix + ("   " if is_last else "│  ")
        folder_children = children.get(str(folder.get("path") or ""), [])
        files = list(folder.get("files") or [])
        entries: list[tuple[str, dict[str, Any]]] = [
            *(('folder', child) for child in folder_children),
            *(('file', file) for file in files),
        ]
        for index, (kind, entry) in enumerate(entries):
            entry_is_last = index == len(entries) - 1
            if kind == "folder":
                append_folder(entry, child_prefix, entry_is_last)
            else:
                file_branch = "└─ " if entry_is_last else "├─ "
                lines.append(f"{child_prefix}{file_branch}{_one_line(entry.get('name') or '未命名文件', 200)}")

    roots = children.get("", [])
    for index, folder in enumerate(roots):
        append_folder(folder, "", index == len(roots) - 1)

    if not roots:
        lines.append("└─ （暂无目录）")
    lines.extend(["```", ""])
    return "\n".join(lines)


def write_generated_snapshot(path: Path, content: str) -> bool:
    """原子更新固定生成文件；内容未变化时不改写。"""
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except (FileNotFoundError, OSError, UnicodeError):
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def write_structure_snapshot(path: Path, catalog: dict[str, Any]) -> bool:
    """渲染并原子更新固定的完整结构快照。"""
    return write_generated_snapshot(path, render_structure_snapshot(catalog))
