#!/usr/bin/env python3
"""本地知识库启动入口。"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from src.kb_scanner import load_json_safe
from src.kb_server import create_server

BASE_DIR = Path(__file__).resolve().parent
PID_FILE = BASE_DIR / ".server.pid"


def server_ready(port: int) -> bool:
    try:
        # 明确禁用环境代理，避免 WSL 的 HTTP_PROXY 把 localhost 请求送到远端代理。
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_when_ready(port: int) -> None:
    for _ in range(40):
        if server_ready(port):
            webbrowser.open(f"http://127.0.0.1:{port}")
            return
        time.sleep(0.15)


def write_pid(port: int) -> None:
    PID_FILE.write_text(json.dumps({"pid": os.getpid(), "port": port}), encoding="utf-8")


def remove_pid() -> None:
    try:
        data = json.loads(PID_FILE.read_text(encoding="utf-8"))
        if data.get("pid") == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except (OSError, json.JSONDecodeError):
        pass


def main() -> int:
    config = load_json_safe(BASE_DIR / "config.json", {})
    parser = argparse.ArgumentParser(description="启动本地知识库")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(config.get("port", 8765)))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    if server_ready(args.port):
        print(f"知识库已在运行：http://127.0.0.1:{args.port}")
        if not args.no_browser:
            webbrowser.open(f"http://127.0.0.1:{args.port}")
        return 0

    try:
        server = create_server(BASE_DIR, args.host, args.port)
    except OSError as exc:
        print(f"无法启动：端口 {args.port} 被占用或不可用：{exc}", file=sys.stderr)
        return 1

    write_pid(args.port)

    def shutdown(_signum: int, _frame: object) -> None:
        # shutdown 必须从其他线程调用，避免 signal handler 与 serve_forever 互锁。
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(args.port,), daemon=True).start()

    print("=" * 54)
    print("  拾页星图 · 本地知识库")
    print(f"  地址：http://127.0.0.1:{args.port}")
    print("  资料目录：library/")
    print("  全局结构：KNOWLEDGE_STRUCTURE.md（自动按需覆盖）")
    print("  按 Ctrl+C 停止服务")
    print("=" * 54)
    try:
        server.serve_forever(poll_interval=0.3)
    finally:
        server.server_close()
        remove_pid()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
