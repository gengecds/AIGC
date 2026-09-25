#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：cdp_chrome.py
作用：专用持久浏览器 —— 固定一个 Chrome 数据目录（profile），用固定端口(9222)拉起，
      保证「登录一次抖音/小红书/YouTube，后续采集自动复用登录态，不再重复登录」。

背景：采集脚本（common.py 的 ab()）通过 `agent-browser --cdp 9222` 连接本机真实 Chrome。
      登录态存在 Chrome 的 profile 目录里。此前用临时目录（/tmp/aigc-cdp-profile）启动，
      目录一换登录态就丢，导致每次都重登。本脚本改为固定持久 profile，登录态长效保存。

用法：
    python scripts/intel/cdp_chrome.py                 # 确保 CDP 9222 在线（不在线则用持久 profile 拉起）
    python scripts/intel/cdp_chrome.py --page https://www.douyin.com/   # 拉起后打开指定页（用于首次登录）
    python scripts/intel/cdp_chrome.py --page douyin   # 支持平台别名：douyin / xiaohongshu / youtube

依赖：
    - 本机已安装 Google Chrome（macOS 默认路径），可用环境变量 AIGC_CHROME_PATH 覆盖
    - 该 profile 目录在 storage/ 下（已被 .gitignore 忽略），不会把登录态提交进 git
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

# 项目根：scripts/intel/cdp_chrome.py → 向上 3 级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 固定持久 profile（storage/ 已被 .gitignore 忽略，登录态不会进 git）
DEFAULT_PROFILE = _PROJECT_ROOT / "storage" / "intel" / "chrome-profile"

CDP_PORT = "9222"
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

# 平台别名 → 登录/首页地址
_PLATFORM_URL = {
    "douyin": "https://www.douyin.com/",
    "xiaohongshu": "https://www.xiaohongshu.com/explore",
    "youtube": "https://www.youtube.com/",
}


def _chrome_path() -> str:
    """定位 Google Chrome 可执行文件，支持环境变量 AIGC_CHROME_PATH 覆盖。"""
    override = os.environ.get("AIGC_CHROME_PATH", "").strip()
    if override and Path(override).exists():
        return override
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    raise RuntimeError(
        "未找到 Google Chrome。请安装 Chrome，或设置环境变量 AIGC_CHROME_PATH 指向 Chrome 可执行文件。"
    )


def is_cdp_online() -> bool:
    """探测 CDP 9222 是否已在 line（返回 True 说明已有可连接的 Chrome）。"""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return bool(data.get("Browser"))
    except Exception:
        return False


def resolve_page(url_or_alias: str = "") -> str:
    """把参数解析为真实 URL：平台别名 → 首页；空 → 空白页（避免默认新标签页干扰）。"""
    if not url_or_alias:
        return "about:blank"
    if url_or_alias in _PLATFORM_URL:
        return _PLATFORM_URL[url_or_alias]
    return url_or_alias


def ensure_chrome(profile: Path = DEFAULT_PROFILE, page: str = "", wait: int = 20) -> bool:
    """确保 CDP 9222 在线；若不在线，用固定持久 profile 启动 Chrome。

    返回 True 表示 CDP 已在线（可能复用已在跑的，也可能本次新拉起的）。
    用持久 profile，登录态会保存，后续采集脚本无需重复登录。
    """
    # 1) 已在连线 → 直接复用（不打扰已打开的浏览器窗口）
    if is_cdp_online():
        print(f"CDP {CDP_PORT} 已在线，复用当前浏览器（登录态保留中）。")
        return True

    # 2) 不在线 → 用固定持久 profile 拉起独立 Chrome 实例
    profile.mkdir(parents=True, exist_ok=True)
    chrome = _chrome_path()
    url = resolve_page(page)
    cmd = [
        chrome,
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--noerrdialogs",
        url,
    ]
    print("本次无在线 CDP，用持久 profile 拉起专用浏览器：")
    print(f"  profile : {profile}")
    # 分离启动：脚本退出后 Chrome 仍独立运行（不阻塞采集/终端）
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    # 3) 轮询等待 CDP 就绪
    for _ in range(int(wait * 5)):  # 每 0.2s 探测一次
        if is_cdp_online():
            print(f"✓ 专用浏览器已启动，CDP {CDP_PORT} 在线（profile 持久，登录态将自动保存）。")
            return True
        time.sleep(0.2)
    print("! 已尝试拉起 Chrome，但 CDP 未在预期时间内就绪，请检查 Chrome 是否被拦截/是否前台运行。")
    return False


def main():
    import argparse
    parser = argparse.ArgumentParser(description="专用持久浏览器（固定 profile + CDP 9222）")
    parser.add_argument("--page", default="", help="拉起后打开的页面 URL 或平台别名（douyin/xiaohongshu/youtube）")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE), help="持久 profile 目录（默认 storage/intel/chrome-profile）")
    args = parser.parse_args()

    ok = ensure_chrome(profile=Path(args.profile), page=args.page)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
