#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：download_douyin_latest.py
作用：抖音最新作品采集 —— 用本地 Chrome（CDP 9222）按关键词搜索，抓取作品详情，
      可选下载音频（yt-dlp 优先 / 页面媒体 URL 降级），落盘到 storage/intel/source/*.json，
      供 IntelAgent 作爆款素材源。

为什么用本地 Chrome + CDP：内容平台对无头浏览器有风控，见 collect_materials.py 说明。

用法：
    python scripts/intel/download_douyin_latest.py "美食"                 # 只采集详情
    python scripts/intel/download_douyin_latest.py "美食" --with-audio    # 同时下载音频（前3条）
    python scripts/intel/download_douyin_latest.py "美食" --limit 12      # 最多抓 12 条
输出：
    storage/intel/source/douyin_<id>_<seq>.json   # 每作品一个文件（IntelAgent 读取）
    storage/intel/audio/*                          # 已下载音频（若 --with-audio）
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    STORAGE_AUDIO,
    ab,
    deep_scroll,
    download_audio,
    eval_js,
    extract_video_id,
    fetch_json_js,
    save_source,
    ensure_dirs,
)

PLATFORM = "抖音"
_VIDEO_ID_RE = r"/video/(\d+)"
_AUDIO_RE = r"/video/(\d+)"


def _drive_search(keyword: str) -> bool:
    """抖音 SPA：直接 URL 直达搜索页时结果 feed 不会自动挂载（只显示首页侧边栏），
    必须在搜索框填入关键词并回车才会真正发起搜索、渲染结果卡片。"""
    kw = json.dumps(keyword, ensure_ascii=False)
    js = (
        "(()=>{"
        "const inp=document.querySelector('[data-e2e=searchbar-input] input, [data-e2e=searchbar-input]');"
        "if(!inp) return 'no-input';"
        "const setter=Object.getOwnPropertyDescriptor(Object.getPrototypeOf(inp),'value').set;"
        "setter.call(inp," + kw + ");"
        "inp.dispatchEvent(new Event('input',{bubbles:true}));"
        "inp.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,bubbles:true}));"
        "return 'ok';"
        "})()"
    )
    r = eval_js(js)
    return "no-input" not in r


def _parse_card(text: str) -> tuple[str, str]:
    """把抖音结果卡片文本解析成 (标题, 作者)。
    卡片文本形如：'合集08:426.6万诏安竟然…@野文不懂吃8小时前' 或 '06:3017.0万…@作者10小时前'。
    开头依次是：合集标记、时长(mm:ss)、播放量(如 6.6万)、正文标题 + #话题，@后跟作者，最后是时间。
    """
    t = (text or "").replace("\n", " ").strip()
    author = ""
    if "@" in t:
        before, after = t.rsplit("@", 1)
        t = before.strip()
        author = after.strip()
        author = re.sub(r"(刚刚|昨天|前天|\d+\s*(分钟|小时|天|周|月)前).*$", "", author).strip()
    t = re.sub(r"^合集\s*", "", t)
    t = re.sub(r"^\d{1,3}:\d{2}\s*", "", t)      # 时长 08:42
    t = re.sub(r"^[\d.]+万?\s*", "", t)          # 播放量 6.6万 / 17.0万
    t = re.sub(r"[#＃]", " ", t)                 # 话题符号改为空格，保留话题文字
    t = re.sub(r"\s+", " ", t).strip()
    return t, author


def collect(keyword: str, limit: int) -> list[dict]:
    """按关键词搜索抖音，抓取结果条目（title / author / url / video_id）。"""
    url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
    print("打开抖音搜索页:", ab("open", url))
    time.sleep(6)
    if not _drive_search(keyword):
        print("! 搜索框未找到，可能页面未加载或结构变化")
        return []
    time.sleep(4)
    deep_scroll(3, 2)

    js = """(()=>{
      const seen = new Set();
      const out = [];
      document.querySelectorAll('a[href*="/video/"]').forEach(a => {
        const m = (a.href || '').match(/\\/video\\/(\\d+)/);
        if (!m) return;
        const vid = m[1];
        if (seen.has(vid)) return;
        seen.add(vid);
        out.push({title: (a.textContent || '').trim(), url: a.href, video_id: vid});
      });
      return JSON.stringify(out);
    })()"""
    items = fetch_json_js(js)
    if not items:
        print("! 未抓到结果（可能被风控/页面结构变化）")
        return []

    out = []
    for it in items:
        vid = extract_video_id(it["url"], _VIDEO_ID_RE)
        if not vid:
            continue
        title, author = _parse_card(it.get("title", ""))
        if not title:
            continue
        out.append({"title": title, "author": author, "url": it["url"], "video_id": vid})
    seen_ids = set()
    dedup = []
    for it in out:
        if it["video_id"] in seen_ids:
            continue
        seen_ids.add(it["video_id"])
        dedup.append(it)
    print(f"抓取到 {len(dedup)} 条去重结果")
    return dedup[:limit]


def main():
    parser = argparse.ArgumentParser(description="抖音最新作品采集（CDP 9222）")
    parser.add_argument("keyword", help="搜索关键词")
    parser.add_argument("--limit", type=int, default=8, help="最多抓取条数（默认 8）")
    parser.add_argument("--with-audio", action="store_true", help="同时下载音频（默认只对前 3 条）")
    parser.add_argument("--audio-limit", type=int, default=3, help="下载音频的上限条数（默认 3）")
    args = parser.parse_args()

    ensure_dirs()
    items = collect(args.keyword, args.limit)
    if not items:
        sys.exit(1)

    for i, it in enumerate(items):
        audio_path = None
        if args.with_audio and i < args.audio_limit:
            audio_path = download_audio(
                it["url"],
                STORAGE_AUDIO / f"douyin_{it['video_id']}.mp3",
                referer=it["url"],
            )
        dest = save_source(
            title=it["title"],
            creator_name=it.get("author", ""),
            platform=PLATFORM,
            url=it["url"],
            video_id=it["video_id"],
            video_file=str(audio_path) if audio_path else "",
        )
        print(f"  ✓ 落盘: {dest}")
    print(f"\n完成：{len(items)} 条抖音素材已写入 storage/intel/source/")


if __name__ == "__main__":
    main()
