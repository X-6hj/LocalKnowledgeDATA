from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.kb_scanner import parse_markdown_meta, scan_library
from src.kb_server import KnowledgeBaseApp, KnowledgeBaseHandler, create_server
from src.knowledge_structure import render_structure_snapshot, write_structure_snapshot
from stop import stop_windows_process, stop_windows_project_processes


class ScannerTests(unittest.TestCase):
    def test_html_is_primary_file_without_hiding_other_direct_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            problem = library / "算法" / "Problem A - 示例"
            problem.mkdir(parents=True)
            (problem / "题解.html").write_text("<!doctype html><title>题解</title>", encoding="utf-8")
            (problem / "题解.md").write_text("# 题解", encoding="utf-8")
            (problem / "题解.cpp").write_text("// AC", encoding="utf-8")

            catalog = scan_library(library)
            folder = next(item for item in catalog["folders"] if item["path"] == "算法/Problem A - 示例")

            self.assertEqual(folder["primary_file"]["name"], "题解.html")
            self.assertEqual({file["name"] for file in folder["files"]}, {"题解.html", "题解.md", "题解.cpp"})

    def test_entry_metadata_overrides_automatic_html_choice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            problem = library / "算法" / "Problem B - 示例"
            problem.mkdir(parents=True)
            (problem / "_说明.md").write_text("---\nentry: 复习版.html\n---\n", encoding="utf-8")
            (problem / "完整题解.html").write_text("<!doctype html>", encoding="utf-8")
            (problem / "复习版.html").write_text("<!doctype html>", encoding="utf-8")

            catalog = scan_library(library)
            folder = next(item for item in catalog["folders"] if item["path"] == "算法/Problem B - 示例")

            self.assertEqual(folder["primary_file"]["name"], "复习版.html")

    def test_folder_without_html_has_no_primary_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            problem = library / "算法" / "Problem C - 示例"
            problem.mkdir(parents=True)
            (problem / "题解.md").write_text("# 题解", encoding="utf-8")

            catalog = scan_library(library)
            folder = next(item for item in catalog["folders"] if item["path"] == "算法/Problem C - 示例")

            self.assertIsNone(folder["primary_file"])

    def test_entry_cannot_escape_its_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            problem = library / "算法" / "Problem D - 示例"
            problem.mkdir(parents=True)
            (problem / "_说明.md").write_text("---\nentry: ../外部.html\n---\n", encoding="utf-8")
            (problem / "安全入口.html").write_text("<!doctype html>", encoding="utf-8")
            (library / "算法" / "外部.html").write_text("<!doctype html>", encoding="utf-8")

            catalog = scan_library(library)
            folder = next(item for item in catalog["folders"] if item["path"] == "算法/Problem D - 示例")

            self.assertEqual(folder["primary_file"]["name"], "安全入口.html")

    def test_catalog_is_cached_until_explicit_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            (library / "主题").mkdir(parents=True)
            app = KnowledgeBaseApp(base)
            first = app.catalog()

            (library / "主题" / "空目录").mkdir()
            self.assertEqual(first["revision"], app.catalog()["revision"])
            second = app.refresh_catalog()
            self.assertNotEqual(first["revision"], second["revision"])
            self.assertIn("主题/空目录", {folder["path"] for folder in second["folders"]})

            (library / "主题" / "空目录").rename(library / "主题" / "已重命名")
            third = app.refresh_catalog()
            self.assertNotEqual(second["revision"], third["revision"])
            self.assertIn("主题/已重命名", {folder["path"] for folder in third["folders"]})

            (library / "主题" / "已重命名").rmdir()
            fourth = app.refresh_catalog()
            self.assertNotEqual(third["revision"], fourth["revision"])
            self.assertIn("主题/已重命名", {folder["path"] for folder in third["folders"]})
            self.assertNotIn("主题/已重命名", {folder["path"] for folder in fourth["folders"]})
            app.close()

    def test_background_refresh_updates_snapshot_without_catalog_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            (library / "初始目录").mkdir(parents=True)
            (base / "config.json").write_text('{"refresh_seconds": 0.05}', encoding="utf-8")
            server = create_server(base, "127.0.0.1", 0)
            try:
                snapshot = base / "KNOWLEDGE_STRUCTURE.md"
                routing = base / "KNOWLEDGE_ROUTING.md"
                self.assertNotIn("后台新增", snapshot.read_text(encoding="utf-8"))
                self.assertNotIn("后台新增", routing.read_text(encoding="utf-8"))
                (library / "后台新增").mkdir()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    if (
                        "后台新增" in snapshot.read_text(encoding="utf-8")
                        and "后台新增" in routing.read_text(encoding="utf-8")
                    ):
                        break
                    time.sleep(0.02)
                self.assertIn("后台新增", snapshot.read_text(encoding="utf-8"))
                self.assertIn("后台新增", routing.read_text(encoding="utf-8"))
            finally:
                server.server_close()

    def test_startup_scan_failure_releases_bound_port(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "library").mkdir()
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            with patch("src.kb_server.scan_library", side_effect=OSError("扫描失败")):
                with self.assertRaises(OSError):
                    create_server(base, "127.0.0.1", port)
            rebound = socket.socket()
            try:
                rebound.bind(("127.0.0.1", port))
            finally:
                rebound.close()

    def test_structure_snapshot_escapes_code_fences_and_shows_real_and_display_names(self) -> None:
        catalog = {
            "revision": "rev",
            "stats": {"folders": 1, "files": 1, "max_depth": 1},
            "folders": [{
                "path": "真实`目录", "parent_path": "", "name": "真实`目录", "title": "展示标题",
                "summary": "危险 ``` 摘要\x00", "child_count": 0,
                "files": [{"name": "代码```片段.md"}],
            }],
        }
        text = render_structure_snapshot(catalog)
        self.assertEqual(text.count("```"), 2)
        self.assertNotIn("\x00", text)
        self.assertIn("真实", text)
        self.assertIn("展示标题", text)

    def test_structure_snapshot_flushes_content_before_atomic_replace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "KNOWLEDGE_STRUCTURE.md"
            catalog = {"revision": "rev", "stats": {}, "folders": []}
            with patch("src.knowledge_structure.os.fsync") as fsync:
                self.assertTrue(write_structure_snapshot(target, catalog))
            fsync.assert_called_once()

    def test_server_startup_creates_fixed_global_structure_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            topic = base / "library" / "算法" / "数论"
            topic.mkdir(parents=True)
            (topic / "_说明.md").write_text("---\nsummary: 整数算法。\nplacement: route\n---\n", encoding="utf-8")
            (topic / "欧几里得.cpp").write_text("// gcd", encoding="utf-8")

            server = create_server(base, "127.0.0.1", 0)
            try:
                snapshot = base / "KNOWLEDGE_STRUCTURE.md"
                routing = base / "KNOWLEDGE_ROUTING.md"
                self.assertTrue(snapshot.is_file())
                self.assertTrue(routing.is_file())
                text = snapshot.read_text(encoding="utf-8")
                routing_text = routing.read_text(encoding="utf-8")
                self.assertIn("library/", text)
                self.assertIn("算法/", text)
                self.assertIn("数论/", text)
                self.assertIn("欧几里得.cpp", text)
                self.assertIn("整数算法。", text)
                self.assertIn("算法/数论", routing_text)
                self.assertIn("整数算法。", routing_text)
                self.assertNotIn("欧几里得.cpp", routing_text)
            finally:
                server.server_close()

    def test_structure_snapshot_failure_does_not_make_catalog_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "library" / "算法").mkdir(parents=True)
            app = KnowledgeBaseApp(base)
            try:
                with patch("src.kb_server.write_structure_snapshot", side_effect=OSError("只读目录")):
                    catalog = app.catalog()
                self.assertEqual(catalog["stats"]["folders"], 1)
            finally:
                app.close()

    def test_structure_snapshot_can_be_regenerated_without_starting_server(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "library" / "课程" / "C++").mkdir(parents=True)
            script = Path(__file__).resolve().parents[1] / "generate_structure.py"

            result = subprocess.run(
                [sys.executable, str(script), "--base-dir", str(base)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("KNOWLEDGE_STRUCTURE.md", result.stdout)
            self.assertIn("KNOWLEDGE_ROUTING.md", result.stdout)
            self.assertIn("C++/", (base / "KNOWLEDGE_STRUCTURE.md").read_text(encoding="utf-8"))
            self.assertIn("课程/C++", (base / "KNOWLEDGE_ROUTING.md").read_text(encoding="utf-8"))

    def test_hidden_ancestors_are_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            (library / ".hidden" / "visible-child").mkdir(parents=True)
            (library / ".hidden" / "visible-child" / "secret.txt").write_text("secret", encoding="utf-8")
            (library / "公开").mkdir()

            catalog = scan_library(library)

            self.assertEqual([folder["path"] for folder in catalog["folders"]], ["公开"])
            self.assertEqual(catalog["stats"]["files"], 0)

    def test_directory_symlinks_and_broken_links_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            library = Path(tmp) / "library"
            library.mkdir()
            external = Path(outside) / "external"
            external.mkdir()
            (external / "outside.txt").write_text("outside", encoding="utf-8")
            link = library / "external-link"
            try:
                link.symlink_to(external, target_is_directory=True)
                (library / "broken-link").symlink_to(Path(outside) / "missing", target_is_directory=True)
            except OSError:
                self.skipTest("当前环境不允许创建目录符号链接")
            (library / "公开").mkdir()

            catalog = scan_library(library)

            self.assertEqual([folder["path"] for folder in catalog["folders"]], ["公开"])
            self.assertEqual(catalog["stats"]["files"], 0)

    def test_legacy_views_keep_description_empty_item_and_modified_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            category = library / "A"
            empty = category / "empty"
            deep = category / "item" / "deep"
            empty.mkdir(parents=True)
            deep.mkdir(parents=True)
            (category / "_分类.md").write_text("---\nsummary: 分类摘要\n---\n", encoding="utf-8")
            deep_file = deep / "new.txt"
            deep_file.write_text("new", encoding="utf-8")
            another_file = deep / "another.txt"
            another_file.write_text("another", encoding="utf-8")
            timestamp = 1_700_000_000
            for folder in (category, empty, category / "item", deep):
                os.utime(folder, (timestamp - 1_000, timestamp - 1_000))
            for file in (deep_file, another_file):
                os.utime(file, (timestamp, timestamp))

            catalog = scan_library(library)

            self.assertEqual(catalog["categories"][0]["description"], "分类摘要")
            self.assertEqual([item["folder"] for item in catalog["items"]], ["A/item"])
            self.assertEqual(catalog["items"][0]["modified"], datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds"))
            self.assertEqual(catalog["types"], {"txt": 1})

    def test_scan_category_item_and_description(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            item = library / "课程" / "C++"
            item.mkdir(parents=True)
            (library / "课程" / "_分类.md").write_text("---\ntitle: 课程资料\nicon: ◇\n---\n", encoding="utf-8")
            (item / "_说明.md").write_text(
                "---\ntitle: C++ 入门\ntags: [C++, 课程]\npinned: true\nsummary: 学习资料\n---\n\n详细说明。",
                encoding="utf-8",
            )
            (item / "讲义.pdf").write_bytes(b"%PDF-test")
            catalog = scan_library(library, {"title": "测试"})
            self.assertEqual(catalog["stats"]["categories"], 1)
            self.assertEqual(catalog["stats"]["items"], 1)
            self.assertEqual(catalog["stats"]["files"], 1)
            self.assertEqual(catalog["categories"][0]["title"], "课程资料")
            self.assertEqual(catalog["items"][0]["title"], "C++ 入门")
            self.assertEqual(catalog["items"][0]["description"], "详细说明。")
            self.assertEqual(catalog["items"][0]["files"][0]["extension"], "pdf")

    def test_loose_file_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            category = library / "单文件"
            category.mkdir(parents=True)
            (category / "备忘.txt").write_text("hello", encoding="utf-8")
            catalog = scan_library(library)
            self.assertEqual(catalog["stats"]["items"], 1)
            self.assertEqual(catalog["items"][0]["title"], "备忘")

    def test_nested_directories_form_navigable_tree_with_local_descriptions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            number_theory = library / "算法" / "数论"
            prime = number_theory / "质数"
            gcd = number_theory / "最大公约数"
            prime.mkdir(parents=True)
            gcd.mkdir(parents=True)
            (library / "算法" / "_分类.md").write_text(
                "---\ntitle: 算法专题\nicon: ∑\nsummary: 算法根目录。\n---\n",
                encoding="utf-8",
            )
            (number_theory / "_说明.md").write_text(
                "---\ntitle: 数论\nsummary: 整数性质与计算方法。\n---\n\n数论目录说明。",
                encoding="utf-8",
            )
            (prime / "_说明.md").write_text(
                "---\ntitle: 质数算法\ntags: [质数, 筛法]\n---\n\n质数子目录说明。",
                encoding="utf-8",
            )
            (prime / "埃氏筛.cpp").write_text("// sieve", encoding="utf-8")
            (gcd / "欧几里得.cpp").write_text("// gcd", encoding="utf-8")

            catalog = scan_library(library)
            folders = {folder["path"]: folder for folder in catalog["folders"]}

            self.assertEqual(catalog["schema_version"], 2)
            self.assertEqual(set(folders), {"算法", "算法/数论", "算法/数论/质数", "算法/数论/最大公约数"})
            self.assertEqual(folders["算法"]["parent_path"], "")
            self.assertEqual(folders["算法/数论"]["parent_path"], "算法")
            self.assertEqual(folders["算法/数论/质数"]["depth"], 3)
            self.assertEqual(folders["算法/数论"]["child_count"], 2)
            self.assertEqual(folders["算法/数论"]["description"], "数论目录说明。")
            self.assertEqual(folders["算法/数论/质数"]["title"], "质数算法")
            self.assertEqual([file["name"] for file in folders["算法/数论/质数"]["files"]], ["埃氏筛.cpp"])
            self.assertEqual(folders["算法/数论"]["files"], [])
            self.assertEqual(folders["算法"]["descendant_file_count"], 2)
            self.assertEqual(folders["算法"]["descendant_file_types"], ["cpp"])
            self.assertEqual(catalog["stats"]["folders"], 4)
            self.assertEqual(catalog["stats"]["max_depth"], 3)
            self.assertEqual(catalog["stats"]["items"], 1)
            self.assertEqual(
                {file["name"] for file in catalog["items"][0]["files"]},
                {"埃氏筛.cpp", "欧几里得.cpp"},
            )

    def test_frontmatter_and_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "_说明.md"
            path.write_text("---\norder: 3\npinned: false\n---\n\n# 标题\n**正文**", encoding="utf-8")
            meta, body = parse_markdown_meta(path)
            self.assertEqual(meta["order"], 3)
            self.assertFalse(meta["pinned"])
            self.assertEqual(body, "标题\n正文")

    def test_ai_routing_snapshot_keeps_reusable_routes_and_omits_leaf_attachments(self) -> None:
        from src.placement_router import render_routing_snapshot

        catalog = {
            "revision": "route-rev",
            "stats": {"folders": 5, "files": 4, "max_depth": 4},
            "folders": [
                {
                    "path": "算法", "parent_path": "", "depth": 1, "name": "算法", "title": "算法专题",
                    "summary": "算法根目录。", "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构", "parent_path": "算法", "depth": 2, "name": "数据结构",
                    "title": "数据结构", "summary": "维护动态信息。", "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构/树状数组", "parent_path": "算法/数据结构", "depth": 3,
                    "name": "树状数组", "title": "树状数组", "summary": "单点修改与前缀聚合。",
                    "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构/树状数组/Problem E", "parent_path": "算法/数据结构/树状数组",
                    "depth": 4, "name": "Problem E", "title": "Problem E", "summary": "一道具体题目。",
                    "child_count": 0, "files": [
                        {"name": "Problem E.html"}, {"name": "Problem E.md"}, {"name": "Problem E.cpp"},
                    ],
                },
                {
                    "path": "算法/数据结构/树状数组/旧题单文件", "parent_path": "算法/数据结构/树状数组",
                    "depth": 4, "name": "旧题单文件", "title": "旧题单文件", "summary": "仅保留一份代码的具体题目。",
                    "child_count": 0, "files": [{"name": "旧题单文件.cpp"}],
                },
            ],
        }

        text = render_routing_snapshot(catalog)

        self.assertIn("AI 选址路由", text)
        self.assertIn("route-rev", text)
        self.assertIn("算法/数据结构/树状数组", text)
        self.assertIn("单点修改与前缀聚合", text)
        self.assertNotIn("Problem E.html", text)
        self.assertNotIn("Problem E.cpp", text)
        self.assertNotIn("算法/数据结构/树状数组/Problem E", text)
        self.assertNotIn("算法/数据结构/树状数组/旧题单文件", text)

    def test_ai_routing_snapshot_is_atomic_and_unchanged_content_is_not_rewritten(self) -> None:
        from src.placement_router import write_routing_snapshot

        catalog = {"revision": "same", "stats": {}, "folders": []}
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "KNOWLEDGE_ROUTING.md"
            self.assertTrue(write_routing_snapshot(target, catalog))
            first_mtime = target.stat().st_mtime_ns
            self.assertFalse(write_routing_snapshot(target, catalog))
            self.assertEqual(target.stat().st_mtime_ns, first_mtime)
            self.assertEqual(list(target.parent.glob(".KNOWLEDGE_ROUTING.md.*.tmp")), [])

    def test_placement_query_prefers_specific_reusable_route_and_reports_duplicate_leaf(self) -> None:
        from src.placement_router import query_placement

        catalog = {
            "revision": "query-rev",
            "folders": [
                {
                    "path": "算法/数据结构", "parent_path": "算法", "depth": 2, "name": "数据结构",
                    "title": "数据结构", "summary": "维护动态信息。", "tags": ["数据结构"],
                    "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构/树状数组", "parent_path": "算法/数据结构", "depth": 3,
                    "name": "树状数组", "title": "树状数组", "summary": "支持单点修改和区间求和。",
                    "tags": ["树状数组", "前缀和"], "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构/树状数组/局部状态动态维护",
                    "parent_path": "算法/数据结构/树状数组", "depth": 4, "name": "局部状态动态维护",
                    "title": "局部状态动态维护", "summary": "单点修改后只重算相邻位置，再做区间求和。",
                    "tags": ["局部重算"], "child_count": 1, "files": [],
                },
                {
                    "path": "算法/数据结构/树状数组/局部状态动态维护/E - 小月的相邻数组",
                    "parent_path": "算法/数据结构/树状数组/局部状态动态维护", "depth": 5,
                    "name": "E - 小月的相邻数组", "title": "E - 小月的相邻数组",
                    "summary": "三点重算与开区间求和。", "tags": ["题目"], "child_count": 0,
                    "files": [{"name": "E - 小月的相邻数组.html"}],
                    "primary_file": {"name": "E - 小月的相邻数组.html"},
                },
                {
                    "path": "算法/动态规划/值域状态 DP", "parent_path": "算法/动态规划", "depth": 3,
                    "name": "值域状态 DP", "title": "值域状态 DP", "summary": "按值域保存状态。",
                    "tags": ["动态规划"], "child_count": 1, "files": [],
                },
            ],
        }

        result = query_placement(
            catalog,
            title="E - 小月的相邻数组",
            keywords="树状数组 单点修改 区间求和 局部重算",
            limit=3,
        )

        self.assertEqual(result["revision"], "query-rev")
        self.assertEqual(result["candidates"][0]["path"], "算法/数据结构/树状数组/局部状态动态维护")
        self.assertNotIn(
            "算法/数据结构/树状数组/局部状态动态维护/E - 小月的相邻数组",
            {candidate["path"] for candidate in result["candidates"]},
        )
        self.assertIn(
            "算法/数据结构/树状数组/局部状态动态维护/E - 小月的相邻数组",
            {duplicate["path"] for duplicate in result["possible_duplicates"]},
        )
        self.assertIn("E - 小月的相邻数组", result["candidates"][0]["direct_children"])
        self.assertLessEqual(len(result["candidates"]), 3)

    def test_placement_query_reports_exact_duplicate_for_short_title(self) -> None:
        from src.placement_router import query_placement

        catalog = {
            "revision": "short-title",
            "folders": [
                {
                    "path": "算法/图论", "parent_path": "算法", "depth": 2, "name": "图论", "title": "图论",
                    "summary": "图算法主题。", "tags": ["图论"], "child_count": 1, "files": [],
                },
            ],
        }

        result = query_placement(catalog, title="图论", limit=3)

        self.assertIn(
            "算法/图论",
            {duplicate["path"] for duplicate in result["possible_duplicates"]},
        )

    def test_placement_query_cli_returns_small_json_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            method = base / "library" / "算法" / "数据结构" / "树状数组"
            method.mkdir(parents=True)
            (method / "_说明.md").write_text(
                "---\nsummary: 支持单点修改与区间求和。\ntags: [树状数组, 前缀和]\n---\n",
                encoding="utf-8",
            )
            script = Path(__file__).resolve().parents[1] / "query_placement.py"

            result = subprocess.run(
                [
                    sys.executable, str(script), "--base-dir", str(base), "--title", "新题",
                    "--keywords", "树状数组 单点修改", "--limit", "2", "--json",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["candidates"][0]["path"], "算法/数据结构/树状数组")
            self.assertLessEqual(len(payload["candidates"]), 2)
            self.assertNotIn("description", result.stdout)

    def test_placement_query_cli_does_not_create_missing_library(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "not-a-project"
            base.mkdir()
            script = Path(__file__).resolve().parents[1] / "query_placement.py"

            result = subprocess.run(
                [sys.executable, str(script), "--base-dir", str(base), "--title", "新题", "--json"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((base / "library").exists())

    def test_explicit_placement_role_overrides_automatic_route_heuristic(self) -> None:
        from src.placement_router import query_placement

        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp) / "library"
            route = library / "算法" / "专题容器"
            leaf = library / "算法" / "占位叶子"
            route.mkdir(parents=True)
            leaf.mkdir(parents=True)
            (route / "_说明.md").write_text(
                "---\nplacement: route\nsummary: 允许继续放置子主题。\n---\n",
                encoding="utf-8",
            )
            for suffix in ("html", "md", "cpp"):
                (route / f"附属说明.{suffix}").write_text("x", encoding="utf-8")
            (leaf / "_说明.md").write_text(
                "---\nplacement: leaf\nsummary: 不接受子主题。\n---\n",
                encoding="utf-8",
            )

            catalog = scan_library(library)
            folders = {folder["path"]: folder for folder in catalog["folders"]}
            self.assertEqual(folders["算法/专题容器"]["placement_role"], "route")
            self.assertEqual(folders["算法/占位叶子"]["placement_role"], "leaf")

            result = query_placement(catalog, keywords="允许继续放置", limit=5)
            paths = {candidate["path"] for candidate in result["candidates"]}
            self.assertIn("算法/专题容器", paths)
            self.assertNotIn("算法/占位叶子", paths)


class StaticFrontendContractTests(unittest.TestCase):
    def test_generated_structure_snapshot_is_private_runtime_output(self) -> None:
        project = Path(__file__).resolve().parents[1]
        gitignore = (project / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/KNOWLEDGE_STRUCTURE.md", gitignore)
        self.assertIn("/KNOWLEDGE_ROUTING.md", gitignore)
        self.assertIn("/.KNOWLEDGE_ROUTING.md.*.tmp", gitignore)

    def test_project_context_requires_query_first_and_forbids_template_copying(self) -> None:
        project = Path(__file__).resolve().parents[1]
        context = (project / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("query_placement.py", context)
        self.assertIn("creative/frontend-design", context)
        self.assertIn("最多 3 个候选", context)
        self.assertIn("不要扫描整个", context)
        self.assertIn("不得直接复制", context)
        self.assertIn("题目", context)
        self.assertIn("教学", context)

    def test_current_folder_exposes_primary_learning_entry(self) -> None:
        project = Path(__file__).resolve().parents[1]
        index = (project / "static" / "index.html").read_text(encoding="utf-8")
        catalog_script = (project / "static" / "catalog.js").read_text(encoding="utf-8")

        self.assertIn('id="currentFolderPrimary"', index)
        self.assertIn("folder.primary_file", catalog_script)
        self.assertIn("打开学习笔记", catalog_script)

    def test_page_exposes_searchable_complete_structure_without_replacing_local_navigation(self) -> None:
        project = Path(__file__).resolve().parents[1]
        index = (project / "static" / "index.html").read_text(encoding="utf-8")
        catalog_script = (project / "static" / "catalog.js").read_text(encoding="utf-8")
        app_script = (project / "static" / "app.js").read_text(encoding="utf-8")
        style = (project / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="structureOpen"', index)
        self.assertIn('<dialog id="structureDialog"', index)
        self.assertIn('id="structureTree"', index)
        self.assertIn('id="structureSearch"', index)
        self.assertIn('id="structureFileToggle"', index)
        self.assertIn('id="categoryList"', index)
        self.assertNotIn('id="globalStructurePanel"', index)
        self.assertIn("function renderGlobalStructure", catalog_script)
        self.assertIn("structureExpanded", catalog_script)
        self.assertIn("data-structure-path", catalog_script)
        self.assertIn("function setupGlobalStructure", app_script)
        self.assertIn("showModal()", app_script)
        self.assertIn("let initialized = false", app_script)
        self.assertIn("if (!initialized)", app_script)
        self.assertIn("if (Store.structureShowFiles)", app_script)
        self.assertIn("if (folder.files.length) Store.structureExpanded.add(folder.path)", app_script)
        self.assertIn("enterFolder(action.dataset.path)", app_script)
        self.assertIn(".structure-scroll { display: block; padding: 9px 8px 8px; overflow: hidden; }", style)
        self.assertIn(".structure-tree { height: 100%;", style)

    def test_complete_structure_supports_resize_search_folding_and_current_reveal(self) -> None:
        project = Path(__file__).resolve().parents[1]
        index = (project / "static" / "index.html").read_text(encoding="utf-8")
        catalog_script = (project / "static" / "catalog.js").read_text(encoding="utf-8")
        app_script = (project / "static" / "app.js").read_text(encoding="utf-8")
        style = (project / "static" / "style.css").read_text(encoding="utf-8")

        self.assertIn('id="structureResizeHandle"', index)
        self.assertIn('role="separator"', index)
        self.assertIn('aria-orientation="vertical"', index)
        self.assertIn("structureSearchCollapsed: new Set()", catalog_script)
        self.assertIn("Store.structureSearchCollapsed.has(folder.path)", catalog_script)
        self.assertIn("function setupStructureResize", app_script)
        self.assertIn('"kb:structure-width"', app_script)
        self.assertIn("setPointerCapture", app_script)
        self.assertIn('setAttribute("aria-valuenow"', app_script)
        self.assertIn("function revealCurrentStructure", app_script)
        self.assertIn('scrollIntoView({ block: "center"', app_script)
        self.assertIn("--structure-dialog-width", style)
        self.assertIn(".structure-resize-handle", style)
        self.assertIn(".structure-resize-hint { display: none; }", style)

    def test_learning_note_safety_reference_is_not_a_copyable_page_template(self) -> None:
        project = Path(__file__).resolve().parents[1]
        old_template = project / "templates" / "学习笔记.html"
        reference = (project / "docs" / "学习页安全与质量底线.md").read_text(encoding="utf-8")
        note_css = (project / "static" / "note.css").read_text(encoding="utf-8")

        self.assertFalse(old_template.exists())
        self.assertIn("不得复制", reference)
        self.assertIn("必须从题目教学模型开始", reference)
        self.assertIn("/static/note.css", reference)
        self.assertIn("script-src 'none'", reference)
        self.assertIn("完整 AC 代码", reference)
        self.assertIn("整体执行流程", reference)
        self.assertIn("prefers-color-scheme", note_css)
        self.assertIn("@media print", note_css)
        self.assertIn(".complete-code", note_css)

    def test_file_only_folder_does_not_show_empty_search_state(self) -> None:
        project = Path(__file__).resolve().parents[1]
        catalog_script = (project / "static" / "catalog.js").read_text(encoding="utf-8")

        self.assertIn("hasCurrentFiles", catalog_script)
        self.assertIn("current.files.length", catalog_script)


class HttpSecurityTests(unittest.TestCase):
    def test_library_html_uses_non_executable_content_security_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            note = base / "library" / "公开" / "note.html"
            note.parent.mkdir(parents=True)
            note.write_text("<!doctype html><title>Note</title>", encoding="utf-8")
            server = create_server(base, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(
                    f"http://127.0.0.1:{server.server_address[1]}/files/%E5%85%AC%E5%BC%80/note.html",
                    timeout=3,
                ) as response:
                    policy = response.headers["Content-Security-Policy"]
                    content_type = response.headers["Content-Type"]
                self.assertTrue(content_type.startswith("text/html"))
                self.assertIn("script-src 'none'", policy)
                self.assertIn("connect-src 'none'", policy)
                self.assertIn("form-action 'none'", policy)
                self.assertIn("style-src 'self'", policy)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_placement_endpoint_returns_compact_candidates_from_cached_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            method = base / "library" / "算法" / "数据结构" / "树状数组"
            method.mkdir(parents=True)
            (method / "_说明.md").write_text(
                "---\nsummary: 支持单点修改和区间求和。\ntags: [树状数组]\n---\n",
                encoding="utf-8",
            )
            server = create_server(base, "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            query = urllib.parse.urlencode({"title": "新题", "keywords": "树状数组 单点修改", "limit": "2"})
            try:
                with opener.open(
                    f"http://127.0.0.1:{server.server_address[1]}/api/placement?{query}",
                    timeout=3,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(response.status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["data"]["candidates"][0]["path"], "算法/数据结构/树状数组")
                self.assertNotIn("description", json.dumps(payload, ensure_ascii=False))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_response_write_ignores_client_disconnect(self) -> None:
        class DisconnectingWriter:
            def __init__(self, error_type: type[OSError]) -> None:
                self.error_type = error_type

            def write(self, data: bytes) -> None:
                raise self.error_type("客户端已断开")

        for error_type in (BrokenPipeError, ConnectionResetError):
            with self.subTest(error_type=error_type.__name__):
                handler = object.__new__(KnowledgeBaseHandler)
                setattr(handler, "send_response", lambda status: None)
                setattr(handler, "send_header", lambda *args: None)
                setattr(handler, "end_headers", lambda: None)
                setattr(handler, "wfile", DisconnectingWriter(error_type))

                handler._json({"ok": True})

    def _request_open(
        self,
        port: int,
        *,
        origin: str | None,
        content_type: str,
    ) -> tuple[int, dict[str, object]]:
        body = json.dumps({"path": "公开/item.txt", "action": "default"}).encode("utf-8")
        headers = {"Content-Type": content_type}
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/open",
            data=body,
            method="POST",
            headers=headers,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            finally:
                exc.close()

    def test_open_action_rejects_cross_origin_and_allows_loopback_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item = base / "library" / "公开" / "item.txt"
            item.parent.mkdir(parents=True)
            item.write_text("ok", encoding="utf-8")
            server = create_server(base, "127.0.0.1", 0)
            app = getattr(server, "app")
            actions: list[tuple[str, str]] = []
            app.perform_file_action = lambda path, action: actions.append((path.name, action))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            try:
                rejected_status, _ = self._request_open(
                    port,
                    origin="https://attacker.example",
                    content_type="application/json",
                )
                self.assertEqual(rejected_status, 403)
                self.assertEqual(actions, [])

                allowed_status, _ = self._request_open(
                    port,
                    origin=f"http://127.0.0.1:{port}",
                    content_type="application/json",
                )
                self.assertEqual(allowed_status, 200)

                localhost_status, _ = self._request_open(
                    port,
                    origin=f"http://localhost:{port}",
                    content_type="application/json",
                )
                self.assertEqual(localhost_status, 200)
                self.assertEqual(actions, [("item.txt", "default"), ("item.txt", "default")])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_open_action_rejects_simple_cross_site_content_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            item = base / "library" / "公开" / "item.txt"
            item.parent.mkdir(parents=True)
            item.write_text("ok", encoding="utf-8")
            server = create_server(base, "127.0.0.1", 0)
            app = getattr(server, "app")
            actions: list[tuple[str, str]] = []
            app.perform_file_action = lambda path, action: actions.append((path.name, action))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                status, _ = self._request_open(
                    server.server_address[1],
                    origin=None,
                    content_type="text/plain;charset=UTF-8",
                )
                self.assertEqual(status, 415)
                self.assertEqual(actions, [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


    def test_health_reports_security_patch_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            server = create_server(Path(tmp), "127.0.0.1", 0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                with opener.open(
                    f"http://127.0.0.1:{server.server_address[1]}/api/health",
                    timeout=3,
                ) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload["data"]["version"], "1.6.0")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class PathSafetyTests(unittest.TestCase):
    @patch("stop.subprocess.run")
    def test_windows_stop_accepts_project_runtime_with_relative_script(self, run_mock) -> None:
        run_mock.return_value = SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as tmp, patch("stop.PID_FILE", Path(tmp) / ".server.pid"):
            result = stop_windows_process(12345)

        self.assertEqual(result, 0)
        script = base64.b64decode(run_mock.call_args.args[0][-1]).decode("utf-16le")
        self.assertIn("ExecutablePath", script)
        project = Path(__file__).resolve().parents[1]
        self.assertIn(str(project / "run.py"), script)
        self.assertIn(str(project / "runtime" / "windows-python"), script)
        self.assertNotIn(f"CommandLine -like '*{project}*'", script)

    @patch("stop.subprocess.run")
    def test_windows_stop_fallback_matches_run_script_and_exact_project_path(self, run_mock) -> None:
        run_mock.return_value = SimpleNamespace(returncode=0)

        result = stop_windows_project_processes()

        self.assertEqual(result, 0)
        command = run_mock.call_args.args[0]
        self.assertEqual(command[:4], ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"])
        script = base64.b64decode(command[-1]).decode("utf-16le")
        self.assertIn("ExecutablePath", script)
        project = Path(__file__).resolve().parents[1]
        self.assertIn(str(project / "run.py"), script)
        self.assertIn(str(project / "runtime" / "windows-python"), script)
        self.assertNotIn(f"CommandLine -like '*{project}*'", script)
        self.assertIn("Get-CimInstance Win32_Process", script)
        self.assertIn("Stop-Process", script)

    def test_library_path_allows_inside_and_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            inside = base / "library" / "分类" / "file.txt"
            inside.parent.mkdir(parents=True)
            inside.write_text("ok", encoding="utf-8")
            app = KnowledgeBaseApp(base)
            self.assertEqual(app.resolve_library_path("分类/file.txt"), inside.resolve())
            with self.assertRaises(PermissionError):
                app.resolve_library_path("../outside.txt")
            app.close()

    @patch("src.kb_server.subprocess.run")
    @patch.object(KnowledgeBaseApp, "windows_path", return_value=r"D:\资料\含 空格&符号.txt")
    @patch("src.kb_server.platform.release", return_value="microsoft-standard-WSL2")
    def test_native_open_uses_encoded_shell_execute(self, _release, _windows_path, run_mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            file_path = base / "library" / "分类" / "含 空格&符号.txt"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("ok", encoding="utf-8")
            run_mock.return_value = SimpleNamespace(returncode=0, stderr=b"")
            app = KnowledgeBaseApp(base)
            app.open_native(file_path)
            command = run_mock.call_args.args[0]
            self.assertEqual(command[:4], [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand"
            ])
            script = base64.b64decode(command[-1]).decode("utf-16le")
            self.assertIn(r"D:\资料\含 空格&符号.txt", script)
            self.assertIn("UseShellExecute = $true", script)
            self.assertNotIn("$args[0]", script)
            self.assertNotIn("-LiteralPath", script)
            app.close()

    @patch("src.kb_server.subprocess.Popen")
    @patch.object(KnowledgeBaseApp, "_uses_windows_bridge", return_value=False)
    @patch("src.kb_server.platform.system", return_value="Windows")
    def test_choose_uses_isolated_windows_dialog_helper(self, _system, _bridge, popen_mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            file_path = base / "library" / "网络" / "学习路线.md"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("ok", encoding="utf-8")
            app = KnowledgeBaseApp(base)

            app.perform_file_action(file_path, "choose")
            command = popen_mock.call_args.args[0]
            self.assertEqual(command, [
                str(base / "runtime" / "windows-python" / "pythonw.exe"),
                str(base / "src" / "windows_open_with.py"),
                str(file_path),
            ])
            app.close()

    @patch("src.kb_server.subprocess.run")
    @patch.object(KnowledgeBaseApp, "windows_path", return_value=r"D:\资料\学习路线.md")
    @patch("src.kb_server.platform.release", return_value="microsoft-standard-WSL2")
    def test_reveal_selects_exact_file_in_explorer(self, _release, _windows_path, run_mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            file_path = base / "library" / "网络" / "学习路线.md"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("ok", encoding="utf-8")
            run_mock.return_value = SimpleNamespace(returncode=0, stderr=b"")
            app = KnowledgeBaseApp(base)

            app.perform_file_action(file_path, "reveal")
            command = run_mock.call_args.args[0]
            script = base64.b64decode(command[-1]).decode("utf-16le")
            self.assertIn(r"C:\Windows\explorer.exe", script)
            self.assertIn('/select,"D:\\资料\\学习路线.md"', script)

            with self.assertRaisesRegex(ValueError, "不支持的文件操作"):
                app.perform_file_action(file_path, "unknown")
            app.close()

    @patch("src.kb_server.subprocess.Popen")
    @patch("src.kb_server.os.startfile", create=True)
    @patch.object(KnowledgeBaseApp, "_uses_windows_bridge", return_value=False)
    @patch("src.kb_server.platform.system", return_value="Windows")
    def test_native_windows_actions_use_shell_associations_and_exact_selection(
        self, _system, _bridge, startfile_mock, popen_mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            file_path = base / "library" / "网络" / "学习路线.md"
            file_path.parent.mkdir(parents=True)
            file_path.write_text("ok", encoding="utf-8")
            app = KnowledgeBaseApp(base)

            app.perform_file_action(file_path, "default")
            startfile_mock.assert_called_with(str(file_path))

            app.perform_file_action(file_path, "choose")
            choose_command = popen_mock.call_args.args[0]
            self.assertEqual(choose_command, [
                str(base / "runtime" / "windows-python" / "pythonw.exe"),
                str(base / "src" / "windows_open_with.py"),
                str(file_path),
            ])

            app.perform_file_action(file_path, "reveal")
            reveal_command = popen_mock.call_args.args[0]
            self.assertTrue(str(reveal_command[0]).lower().endswith("explorer.exe"))
            self.assertEqual(reveal_command[1:], ["/select,", str(file_path)])
            app.close()


if __name__ == "__main__":
    unittest.main()
