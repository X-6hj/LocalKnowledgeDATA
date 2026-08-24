"""知识库目录扫描与元数据解析。仅使用 Python 标准库。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

DESCRIPTION_NAMES = ("_说明.md", "_meta.md", "说明.md", "README.md")
CATEGORY_META_NAMES = ("_分类.md", "_category.md")
METADATA_NAMES = {name.lower() for name in DESCRIPTION_NAMES + CATEGORY_META_NAMES}
IGNORED_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}
IGNORED_SUFFIXES = {".tmp", ".part", ".crdownload", ".lnk"}


def load_json_safe(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else default
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value.strip("'\"")


def parse_markdown_meta(path: Path | None) -> tuple[dict[str, Any], str]:
    """解析简单 YAML 风格头信息；正文按纯文本安全展示。"""
    if path is None or not path.exists():
        return {}, ""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}, ""
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line and not line.lstrip().startswith("#"):
                    key, value = line.split(":", 1)
                    meta[key.strip().lower()] = _parse_scalar(value)
            body = parts[2]
    body = body.strip()
    plain = re.sub(r"(?m)^#{1,6}\s+", "", body)
    plain = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", plain)
    plain = re.sub(r"(?<!\\)[*_`]{1,3}", "", plain)
    return meta, plain.strip()


def _select_meta_file(lookup: dict[str, Path], names: tuple[str, ...]) -> Path | None:
    for name in names:
        if name.lower() in lookup:
            return lookup[name.lower()]
    return None


def _safe_mtime(path: Path | None) -> float:
    if path is None:
        return 0.0
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _format_time(timestamp: float) -> str:
    if not timestamp:
        return "未知"
    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _is_link_like(path: Path) -> bool:
    """符号链接和 Windows junction 都不越界跟随。"""
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _visible_file(path: Path) -> bool:
    return (
        path.is_file()
        and not _is_link_like(path)
        and not path.name.startswith(".")
        and path.name not in IGNORED_NAMES
        and path.suffix.lower() not in IGNORED_SUFFIXES
    )


def _visible_directory(path: Path) -> bool:
    return path.is_dir() and not _is_link_like(path) and not path.name.startswith(".")


def _walk_visible(library_dir: Path) -> Iterator[tuple[Path, list[Path], list[Path]]]:
    """按唯一规则遍历目录树，并在隐藏目录或符号链接处剪枝。"""
    for root_text, directory_names, file_names in os.walk(library_dir, followlinks=False):
        root = Path(root_text)
        directory_names[:] = [
            name for name in sorted(directory_names, key=str.casefold)
            if _visible_directory(root / name)
        ]
        files = [
            root / name for name in sorted(file_names, key=str.casefold)
            if _visible_file(root / name)
        ]
        yield root, [root / name for name in directory_names], files


def _file_record(path: Path, library_dir: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "name": path.name,
        "relative_path": path.relative_to(library_dir).as_posix(),
        "extension": path.suffix.lower().lstrip(".") or "file",
        "size": stat.st_size,
        "modified": _format_time(stat.st_mtime),
    }


def _select_primary_file(meta: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any] | None:
    """选择本目录首选入口；显式 entry 优先，否则回退到首个 HTML。"""
    requested = str(meta.get("entry") or "").strip()
    if requested and "/" not in requested and "\\" not in requested:
        for file in files:
            if file["name"] == requested:
                return file
    return next((file for file in files if file["extension"] in {"html", "htm"}), None)


def _integer(value: Any, default: int = 9999) -> int:
    text = str(value)
    return int(value) if text.lstrip("-").isdigit() else default


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(tag).strip() for tag in value if str(tag).strip()]
    if isinstance(value, str):
        return [tag.strip() for tag in re.split(r"[,，]", value) if tag.strip()]
    return []


def _directory_record(folder: Path, library_dir: Path) -> dict[str, Any]:
    """把任意深度目录转换为统一节点；节点只拥有直属文件。"""
    relative = folder.relative_to(library_dir)
    path = relative.as_posix()
    depth = len(relative.parts)

    try:
        entries = sorted(folder.iterdir(), key=lambda entry: entry.name.casefold())
    except OSError:
        entries = []
    file_lookup = {entry.name.lower(): entry for entry in entries if entry.is_file()}
    category_path = _select_meta_file(file_lookup, CATEGORY_META_NAMES) if depth == 1 else None
    description_path = _select_meta_file(file_lookup, DESCRIPTION_NAMES)
    category_meta, category_body = parse_markdown_meta(category_path)
    description_meta, description_body = parse_markdown_meta(description_path)
    meta = {**category_meta, **description_meta}
    description = description_body or category_body

    files: list[dict[str, Any]] = []
    children: list[Path] = []
    for entry in entries:
        if _visible_directory(entry):
            children.append(entry)
        elif _visible_file(entry) and entry.name.lower() not in METADATA_NAMES:
            try:
                files.append(_file_record(entry, library_dir))
            except OSError:
                continue

    latest = max(
        [_safe_mtime(folder), _safe_mtime(category_path), _safe_mtime(description_path)]
        + [datetime.fromisoformat(file["modified"]).timestamp() for file in files]
    )
    summary = str(meta.get("summary") or (description.split("\n\n", 1)[0] if description else ""))
    placement_role = str(meta.get("placement") or "auto").strip().casefold()
    if placement_role not in {"auto", "route", "leaf"}:
        placement_role = "auto"
    parent_path = relative.parent.as_posix() if depth > 1 else ""
    if parent_path == ".":
        parent_path = ""
    return {
        "id": hashlib.sha1(path.encode("utf-8")).hexdigest()[:12],
        "path": path,
        "parent_path": parent_path,
        "depth": depth,
        "name": folder.name,
        "title": str(meta.get("title") or folder.name),
        "summary": summary or "暂无说明。可在此目录添加 _说明.md。",
        "description": description or "暂无详细说明。",
        "icon": str(meta.get("icon") or ("◇" if depth == 1 else "›")),
        "tags": _tags(meta.get("tags", [])),
        "placement_role": placement_role,
        "pinned": bool(meta.get("pinned", False)),
        "order": _integer(meta.get("order", 9999)),
        "files": files,
        "primary_file": _select_primary_file(meta, files),
        "file_types": sorted({file["extension"] for file in files}),
        "child_count": len(children),
        "descendant_count": 0,
        "descendant_file_count": len(files),
        "descendant_file_types": sorted({file["extension"] for file in files}),
        "modified": _format_time(latest),
        "_modified_timestamp": latest,
        "_has_description_body": bool(description_body),
        "_legacy_category_description": str(category_meta.get("summary") or category_body),
    }


def _legacy_item(folder: dict[str, Any], category: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": folder["id"],
        "title": folder["title"],
        "category": category["key"],
        "category_title": category["title"],
        "summary": folder["summary"],
        "description": folder["description"],
        "tags": folder["tags"],
        "pinned": folder["pinned"],
        "order": folder["order"],
        "folder": folder["path"],
        "files": files,
        "modified": folder["modified"],
        "file_types": sorted({file["extension"] for file in files}),
    }


def _loose_file_item(file: dict[str, Any], category: dict[str, Any], parent_path: str) -> dict[str, Any]:
    relative_path = file["relative_path"]
    return {
        "id": hashlib.sha1(relative_path.encode("utf-8")).hexdigest()[:12],
        "title": Path(file["name"]).stem,
        "category": category["key"],
        "category_title": category["title"],
        "summary": f"{Path(file['name']).suffix.upper().lstrip('.') or '文件'} 文档",
        "description": "此文件直接放在目录中。可以为所在目录添加 _说明.md。",
        "tags": [],
        "pinned": False,
        "order": 9999,
        "folder": parent_path,
        "files": [file],
        "modified": file["modified"],
        "file_types": [file["extension"]],
    }


def tree_signature(library_dir: Path) -> str:
    """快速计算可见目录树签名，不解析目录元数据与正文。"""
    digest = hashlib.sha1()
    if not library_dir.exists():
        return digest.hexdigest()
    for root, directories, files in _walk_visible(library_dir):
        for directory in directories:
            relative = directory.relative_to(library_dir).as_posix()
            digest.update(f"D|{relative}\n".encode("utf-8"))
        for path in files:
            try:
                stat = path.stat()
                relative = path.relative_to(library_dir).as_posix()
                digest.update(f"F|{relative}|{stat.st_mtime_ns}|{stat.st_size}\n".encode("utf-8"))
            except OSError:
                continue
    return digest.hexdigest()


def _scan_folders(library_dir: Path) -> list[dict[str, Any]]:
    paths = [root for root, _directories, _files in _walk_visible(library_dir) if root != library_dir]
    return [_directory_record(path, library_dir) for path in paths]


def _aggregate_descendants(folders: list[dict[str, Any]]) -> None:
    by_path = {folder["path"]: folder for folder in folders}
    for folder in folders:
        folder["_descendant_type_set"] = set(folder["file_types"])
    for folder in reversed(folders):
        parent = by_path.get(folder["parent_path"])
        if parent is None:
            continue
        parent["descendant_count"] += 1 + folder["descendant_count"]
        parent["descendant_file_count"] += folder["descendant_file_count"]
        parent["_descendant_type_set"].update(folder["_descendant_type_set"])
        parent["_modified_timestamp"] = max(parent["_modified_timestamp"], folder["_modified_timestamp"])
    for folder in folders:
        folder["descendant_file_types"] = sorted(folder.pop("_descendant_type_set"))
        folder["modified"] = _format_time(folder["_modified_timestamp"])


def _legacy_catalog_views(folders: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """隔离旧版 categories/items 映射，避免兼容语义进入主目录模型。"""
    categories = [
        {
            "key": folder["path"],
            "title": folder["title"],
            "description": folder["_legacy_category_description"],
            "icon": folder["icon"],
            "order": folder["order"],
        }
        for folder in folders if folder["depth"] == 1
    ]
    category_by_key = {category["key"]: category for category in categories}
    files_by_second_level: dict[str, list[dict[str, Any]]] = {}
    for folder in folders:
        parts = folder["path"].split("/")
        if len(parts) >= 2:
            files_by_second_level.setdefault("/".join(parts[:2]), []).extend(folder["files"])

    items: list[dict[str, Any]] = []
    for folder in folders:
        category = category_by_key.get(folder["path"].split("/", 1)[0])
        if category is None:
            continue
        if folder["depth"] == 2 and (files_by_second_level.get(folder["path"]) or folder["_has_description_body"]):
            items.append(_legacy_item(folder, category, files_by_second_level.get(folder["path"], [])))
        elif folder["depth"] == 1:
            items.extend(_loose_file_item(file, category, folder["path"]) for file in folder["files"])
    items.sort(key=lambda item: (not item["pinned"], item["order"], item["title"].casefold()))
    return categories, items


def scan_library(library_dir: Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """扫描任意深度目录，同时保留旧版 categories/items 字段兼容性。"""
    library_dir.mkdir(parents=True, exist_ok=True)
    folders = _scan_folders(library_dir)
    _aggregate_descendants(folders)
    folders.sort(key=lambda folder: (
        folder["parent_path"], not folder["pinned"], folder["order"], folder["title"].casefold()
    ))
    categories, items = _legacy_catalog_views(folders)
    for folder in folders:
        folder.pop("_modified_timestamp", None)
        folder.pop("_has_description_body", None)
        folder.pop("_legacy_category_description", None)

    all_files = [file for folder in folders for file in folder["files"]]
    type_counts: dict[str, int] = {}
    for item in items:
        for extension in item["file_types"]:
            type_counts[extension] = type_counts.get(extension, 0) + 1

    return {
        "schema_version": 2,
        "revision": tree_signature(library_dir),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "site": config or {},
        "stats": {
            "categories": len(categories),
            "folders": len(folders),
            "max_depth": max((folder["depth"] for folder in folders), default=0),
            "items": len(items),
            "files": len(all_files),
        },
        "types": dict(sorted(type_counts.items())),
        "categories": categories,
        "folders": folders,
        "items": items,
    }
