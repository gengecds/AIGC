#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：download_all_platform_latest.py
作用：一键全平台采集 —— 依序调用抖音 / 小红书 / YouTube 三个平台采集脚本，
      把各平台最新作品落盘到 storage/intel/source/*.json，供 IntelAgent 作爆款素材源。

用法：
    python scripts/intel/download_all_platform_latest.py "美食"                 # 三个平台都采集（默认各 8 条）
    python scripts/intel/download_all_platform_latest.py "美食" --limit 12      # 每个平台最多 12 条
    python scripts/intel/download_all_platform_latest.py "美食" --with-audio    # 同时下载音频
    python scripts/intel/download_all_platform_latest.py "美食" --platforms douyin,youtube  # 只采指定平台
"""

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

_PLATFORM_MAP = {
    "douyin": "download_douyin_latest.py",
    "xiaohongshu": "download_xiaohongshu_latest.py",
    "youtube": "download_youtube_latest.py",
}


def run_one(script: str, keyword: str, limit: int, with_audio: bool) -> int:
    """运行单个平台采集脚本，返回 subprocess 退出码。"""
    cmd = [sys.executable, str(_HERE / script), keyword, "--limit", str(limit)]
    if with_audio:
        cmd.append("--with-audio")
    print(f"\n=== 运行 {script} ${' '.join(cmd[3:])} ===")
    r = subprocess.run(cmd)
    return r.returncode


def main():
    parser = argparse.ArgumentParser(description="一键全平台采集（抖音/小红书/YouTube）")
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=8, help="每个平台最多抓取条数（默认 8）")
    parser.add_argument("--with-audio", action="store_true", help="同时下载音频")
    parser.add_argument("--platforms", default="douyin,xiaohongshu,youtube",
                        help="逗号分隔的平台，可选 douyin,xiaohongshu,youtube（默认全采）")
    args = parser.parse_args()

    wanted = [p.strip() for p in args.platforms.split(",") if p.strip() in _PLATFORM_MAP]
    if not wanted:
        print("! 未指定有效平台（可用 douyin / xiaohongshu / youtube）")
        sys.exit(1)

    failed = []
    for p in wanted:
        rc = run_one(_PLATFORM_MAP[p], args.keyword, args.limit, args.with_audio)
        if rc != 0:
            failed.append(p)

    if failed:
        print(f"\n⚠ 部分平台失败（未采到结果/被风控）：{', '.join(failed)}")
        sys.exit(1)
    print(f"\n✓ 全平台采集完成：{len(wanted)} 个平台已把素材写入 storage/intel/source/")


if __name__ == "__main__":
    main()
