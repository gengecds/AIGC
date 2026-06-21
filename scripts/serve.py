#!/usr/bin/env python3
"""
本地预览服务器 — 启动后浏览器打开就能看漫画前端 + 视频
"""

import http.server
import socketserver
import os
import sys
import webbrowser
from pathlib import Path

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 4333
DIR = Path(__file__).parent.parent / "frontend"

class CORSHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DIR), **kwargs)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        print(f"[{self.log_date_time_string()}] {args[0]} {args[1]} {args[2]}")

print(f"✨ 漫剧工坊预览服务器")
print(f"   URL:  http://localhost:{PORT}")
print(f"   目录: {DIR}")
print()

with socketserver.TCPServer(("", PORT), CORSHandler) as httpd:
    webbrowser.open(f"http://localhost:{PORT}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止")
