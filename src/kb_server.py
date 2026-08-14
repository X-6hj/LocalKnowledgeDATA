"""本地知识库 HTTP 服务。默认只监听 127.0.0.1。"""
from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import platform
import re
import signal
import socket
import subprocess
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .kb_scanner import load_json_safe, scan_library


APP_VERSION = "1.3.1"


class ReuseThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def server_bind(self) -> None:
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except (AttributeError, OSError):
            pass
        super().server_bind()

    def server_close(self) -> None:
        try:
            super().server_close()
        finally:
            app = getattr(self, "app", None)
            if app is not None:
                app.close()


class KnowledgeBaseApp:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir.resolve()
        self.library_dir = (self.base_dir / "library").resolve()
        self.static_dir = (self.base_dir / "static").resolve()
        self.config_path = self.base_dir / "config.json"
        self._cache_lock = threading.Lock()
        self._catalog: dict[str, Any] | None = None
        self._revision = ""
        self.logger = self._setup_logger()

    def _setup_logger(self) -> logging.Logger:
        log_dir = self.base_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"local_knowledge_base.{id(self)}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = RotatingFileHandler(
            log_dir / "server.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8", delay=True
        )
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
        logger.addHandler(handler)
        return logger

    def close(self) -> None:
        """关闭日志文件句柄，便于 Windows 正常停止、测试与移动目录。"""
        for handler in list(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)

    def config(self) -> dict[str, Any]:
        defaults = {
            "title": "拾页星图 · 本地知识库",
            "subtitle": "把散落的资料，整理成可抵达的知识坐标。",
            "port": 8765,
            "refresh_seconds": 5,
        }
        defaults.update(load_json_safe(self.config_path, {}))
        return defaults

    def catalog(self) -> dict[str, Any]:
        # 扫描函数会计算稳定修订号；缓存只用于同一修订号，避免返回陈旧对象。
        fresh = scan_library(self.library_dir, self.config())
        with self._cache_lock:
            if self._catalog is None or fresh["revision"] != self._revision:
                self._catalog = fresh
                self._revision = fresh["revision"]
                self.logger.info(
                    "索引已更新：%s 个分类，%s 个条目，%s 个文件",
                    fresh["stats"]["categories"], fresh["stats"]["items"], fresh["stats"]["files"]
                )
            return self._catalog

    def resolve_library_path(self, relative_path: str) -> Path:
        decoded = urllib.parse.unquote(relative_path).replace("\\", "/").lstrip("/")
        candidate = (self.library_dir / decoded).resolve()
        try:
            candidate.relative_to(self.library_dir)
        except ValueError as exc:
            raise PermissionError("路径超出知识库目录") from exc
        if not candidate.is_file():
            raise FileNotFoundError(decoded)
        return candidate

    @staticmethod
    def windows_path(path: Path) -> str:
        try:
            result = subprocess.run(
                ["wslpath", "-w", str(path)], capture_output=True, text=True, timeout=3, check=True
            )
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return str(path)

    @staticmethod
    def _uses_windows_bridge() -> bool:
        return "microsoft" in platform.release().lower() or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()

    @staticmethod
    def _ps_quote(value: str) -> str:
        """PowerShell 单引号字面量；Windows 文件名本身不允许双引号。"""
        return "'" + value.replace("'", "''") + "'"

    @classmethod
    def _run_windows_powershell(cls, script: str) -> None:
        """用 EncodedCommand 穿过 WSL 边界，避免中文、空格和代码页破坏参数。"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=12, check=False
        )
        if result.returncode != 0:
            error = result.stderr.decode("gbk", "replace").strip() or f"PowerShell 返回 {result.returncode}"
            raise OSError(error)

    @classmethod
    def _shell_execute_script(cls, target: str, arguments: str = "") -> str:
        target_literal = cls._ps_quote(target)
        arguments_literal = cls._ps_quote(arguments)
        return (
            "$psi = New-Object System.Diagnostics.ProcessStartInfo; "
            f"$psi.FileName = {target_literal}; "
            f"$psi.Arguments = {arguments_literal}; "
            "$psi.UseShellExecute = $true; "
            "$process = [System.Diagnostics.Process]::Start($psi); "
            "if ($null -eq $process) { throw 'Windows ShellExecute failed' }"
        )

    def open_native(self, path: Path) -> None:
        """使用系统默认文件关联打开；无关联时返回真实错误。"""
        system = platform.system().lower()
        if self._uses_windows_bridge():
            self._run_windows_powershell(self._shell_execute_script(self.windows_path(path)))
        elif system == "windows":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif system == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])

    def choose_application(self, path: Path) -> None:
        """在独立 Windows 进程中显示原生“打开方式”对话框。"""
        system = platform.system().lower()
        bridge = self._uses_windows_bridge()
        if system != "windows" and not bridge:
            raise OSError("选择应用功能目前仅支持 Windows 或 WSL")

        pythonw = self.base_dir / "runtime" / "windows-python" / "pythonw.exe"
        helper = self.base_dir / "src" / "windows_open_with.py"
        if bridge:
            command = [str(pythonw), self.windows_path(helper), self.windows_path(path)]
        else:
            command = [str(pythonw), str(helper), str(path)]
        subprocess.Popen(command)

    def reveal_in_folder(self, path: Path) -> None:
        """打开资源管理器并选中目标文件，而不是仅进入某个默认目录。"""
        system = platform.system().lower()
        if self._uses_windows_bridge():
            win_path = self.windows_path(path)
            self._run_windows_powershell(
                self._shell_execute_script(
                    r"C:\Windows\explorer.exe", f'/select,"{win_path}"'
                )
            )
        elif system == "windows":
            explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen([str(explorer), "/select,", str(path)])
        elif system == "darwin":
            subprocess.Popen(["open", "-R", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path.parent)])

    def perform_file_action(self, path: Path, action: str) -> None:
        actions = {
            "default": ("默认打开", self.open_native),
            "choose": ("选择应用", self.choose_application),
            "reveal": ("打开所在文件夹", self.reveal_in_folder),
        }
        if action not in actions:
            raise ValueError("不支持的文件操作")
        label, handler = actions[action]
        handler(path)
        self.logger.info("%s：%s", label, path.relative_to(self.library_dir))


class KnowledgeBaseHandler(BaseHTTPRequestHandler):
    server_version = f"LocalKnowledgeBase/{APP_VERSION}"

    @property
    def app(self) -> KnowledgeBaseApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        self.app.logger.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self._security_headers()
        self.end_headers()
        self._write_body(data)

    def _write_body(self, data: bytes) -> bool:
        try:
            self.wfile.write(data)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _error(self, message: str, status: int) -> None:
        self._json({"ok": False, "data": None, "error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        length = min(int(self.headers.get("Content-Length", "0") or 0), 16_384)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._json({"ok": True, "data": {"status": "ready", "version": APP_VERSION}, "error": None})
            return
        if path == "/api/catalog":
            self._json({"ok": True, "data": self.app.catalog(), "error": None})
            return
        if path.startswith("/files/"):
            self._serve_library_file(path[len("/files/"):])
            return
        if path == "/":
            self._serve_static("index.html")
            return
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return
        self._error("未找到该地址", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/open":
            self._error("未找到该地址", HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin")
        if origin:
            port = int(getattr(self.server, "server_port"))
            allowed_origins = {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            }
            if origin.rstrip("/") not in allowed_origins:
                self._error("禁止跨站触发本机文件操作", HTTPStatus.FORBIDDEN)
                return
        if self.headers.get_content_type() != "application/json":
            self._error("文件操作接口只接受 application/json", HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return
        try:
            body = self._read_json()
            relative_path = str(body.get("path", ""))
            action = str(body.get("action", "default"))
            if not relative_path:
                raise ValueError("缺少文件路径")
            file_path = self.app.resolve_library_path(relative_path)
            self.app.perform_file_action(file_path, action)
            self._json({"ok": True, "data": {"path": relative_path, "action": action}, "error": None})
        except json.JSONDecodeError:
            self._error("请求格式不是有效 JSON", HTTPStatus.BAD_REQUEST)
        except (ValueError, FileNotFoundError, PermissionError) as exc:
            self._error(str(exc), HTTPStatus.BAD_REQUEST)
        except (OSError, subprocess.SubprocessError) as exc:
            self.app.logger.exception("打开文件失败")
            self._error(f"系统无法打开该文件：{exc}", HTTPStatus.INTERNAL_SERVER_ERROR)

    def _serve_static(self, relative: str) -> None:
        relative = urllib.parse.unquote(relative).lstrip("/")
        candidate = (self.app.static_dir / relative).resolve()
        try:
            candidate.relative_to(self.app.static_dir)
        except ValueError:
            self._error("禁止访问该路径", HTTPStatus.FORBIDDEN)
            return
        if not candidate.is_file():
            self._error("静态资源不存在", HTTPStatus.NOT_FOUND)
            return
        self._send_file(candidate, cache=False)

    def _serve_library_file(self, relative: str) -> None:
        try:
            candidate = self.app.resolve_library_path(relative)
            self._send_file(candidate, cache=False, allow_range=True)
        except PermissionError:
            self._error("禁止访问该路径", HTTPStatus.FORBIDDEN)
        except FileNotFoundError:
            self._error("文件不存在，可能刚被移动或删除", HTTPStatus.NOT_FOUND)

    def _send_file(self, path: Path, cache: bool, allow_range: bool = False) -> None:
        size = path.stat().st_size
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end, status = 0, max(0, size - 1), HTTPStatus.OK
        range_header = self.headers.get("Range", "") if allow_range else ""
        match = re.match(r"bytes=(\d*)-(\d*)$", range_header)
        if match and size:
            raw_start, raw_end = match.groups()
            if raw_start:
                start = int(raw_start)
                end = int(raw_end) if raw_end else size - 1
            elif raw_end:
                start = max(0, size - int(raw_end))
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = 0 if size == 0 else end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "private, max-age=0" if cache else "no-cache, no-store, must-revalidate")
        self._security_headers()
        self.end_headers()
        if length:
            with path.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    chunk = handle.read(min(65_536, remaining))
                    if not chunk:
                        break
                    if not self._write_body(chunk):
                        break
                    remaining -= len(chunk)


def create_server(base_dir: Path, host: str = "127.0.0.1", port: int = 8765) -> ReuseThreadingHTTPServer:
    app = KnowledgeBaseApp(base_dir)
    server = ReuseThreadingHTTPServer((host, port), KnowledgeBaseHandler)
    server.app = app  # type: ignore[attr-defined]
    return server
