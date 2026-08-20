from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.kb_scanner import parse_markdown_meta, scan_library
from src.kb_server import KnowledgeBaseApp, KnowledgeBaseHandler, create_server
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

    def test_empty_directory_changes_revision_and_catalog_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            library = base / "library"
            (library / "主题").mkdir(parents=True)
            app = KnowledgeBaseApp(base)
            first = app.catalog()

            (library / "主题" / "空目录").mkdir()
            second = app.catalog()
            self.assertNotEqual(first["revision"], second["revision"])
            self.assertIn("主题/空目录", {folder["path"] for folder in second["folders"]})

            (library / "主题" / "空目录").rename(library / "主题" / "已重命名")
            third = app.catalog()
            self.assertNotEqual(second["revision"], third["revision"])
            self.assertIn("主题/已重命名", {folder["path"] for folder in third["folders"]})

            (library / "主题" / "已重命名").rmdir()
            fourth = app.catalog()
            self.assertNotEqual(third["revision"], fourth["revision"])
            self.assertIn("主题/已重命名", {folder["path"] for folder in third["folders"]})
            self.assertNotIn("主题/已重命名", {folder["path"] for folder in fourth["folders"]})
            app.close()

    def test_server_startup_creates_fixed_global_structure_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            topic = base / "library" / "算法" / "数论"
            topic.mkdir(parents=True)
            (topic / "_说明.md").write_text("---\nsummary: 整数算法。\n---\n", encoding="utf-8")
            (topic / "欧几里得.cpp").write_text("// gcd", encoding="utf-8")

            server = create_server(base, "127.0.0.1", 0)
            try:
                snapshot = base / "KNOWLEDGE_STRUCTURE.md"
                self.assertTrue(snapshot.is_file())
                text = snapshot.read_text(encoding="utf-8")
                self.assertIn("library/", text)
                self.assertIn("算法/", text)
                self.assertIn("数论/", text)
                self.assertIn("欧几里得.cpp", text)
                self.assertIn("整数算法。", text)
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
            self.assertIn("C++/", (base / "KNOWLEDGE_STRUCTURE.md").read_text(encoding="utf-8"))

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


class StaticFrontendContractTests(unittest.TestCase):
    def test_generated_structure_snapshot_is_private_runtime_output(self) -> None:
        project = Path(__file__).resolve().parents[1]
        gitignore = (project / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("/KNOWLEDGE_STRUCTURE.md", gitignore)

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

    def test_learning_note_template_is_offline_and_csp_compatible(self) -> None:
        project = Path(__file__).resolve().parents[1]
        template = (project / "templates" / "学习笔记.html").read_text(encoding="utf-8")
        note_css = (project / "static" / "note.css").read_text(encoding="utf-8")

        self.assertIn('href="/static/note.css"', template)
        self.assertNotIn("<script", template.lower())
        self.assertNotIn("http://", template.lower())
        self.assertNotIn("https://", template.lower())
        self.assertNotIn("style=", template.lower())
        self.assertIn("prefers-color-scheme", note_css)
        self.assertIn("@media print", note_css)
        self.assertIn('id="full-code"', template)
        self.assertIn("完整 AC 代码", template)
        self.assertIn("整体执行流程", template)
        self.assertIn('class="complete-code"', template)
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
                self.assertEqual(payload["data"]["version"], "1.5.0")
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
