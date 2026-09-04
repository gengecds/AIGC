#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：download_youtube_latest.py
作用：YouTube 最新视频采集 —— 用本地 Chrome（CDP 9222）按关键词搜索，抓取视频详情，
      可选下载音频（yt-dlp 优先，YouTube 无页面媒体 URL 降级路径，仅靠 CDP 拿详情），
      落盘到 storage/intel/source/*.json，供 IntelAgent 作爆款素材源。

为什么用本地 Chrome + CDP：内容平台对无头浏览器有风控，且 YouTube 作者/标题在真实登录态下最稳。

用法：
    python scripts/intel/download_youtube_latest.py "vlog"                 # 只采集详情
    python scripts/intel/download_youtube_latest.py "vlog" --with-audio    # 同时下载音频（前3条）
    python scripts/intel/download_youtube_latest.py "vlog" --limit 12      # 最多抓 12 条
输出：
    storage/intel/source/youtube_<videoId>_<seq>.json   # 每视频一个文件（IntelAgent 读取）
    storage/intel/audio/*                                # 已下载音频（若 --with-audio）
"""

import argparse
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
    extract_video_id,
    fetch_json_js,
    save_source,
    ensure_dirs,
)

PLATFORM = "YouTube"
_VIDEO_ID_RE = r"[?&]v=([\w-]{11})"


def collect(keyword: str, limit: int) -> list[dict]:
    """按关键词搜索 YouTube，抓取结果条目（title / author / url / video_id）。"""
    url = f"https://www.youtube.com/results?search_query={quote(keyword)}"
    print("打开 YouTube 搜索页:", ab("open", url))
    time.sleep(6)
    deep_scroll(2, 2)

    # 只从「结果卡片」容器取标题，避免命中播放器内部的占位时间/「正在播放」文本。
    # 每张卡片是 ytd-video-renderer（旧列表）或 ytd-rich-item-renderer（新网格）。
    js = """(()=>{
      const out = [];
      const seen = new Set();
      document.querySelectorAll('ytd-video-renderer, ytd-rich-item-renderer').forEach(card => {
        const a = card.querySelector('a#video-title');
        if (!a) return;
        const href = a.href || '';
        const m = href.match(/[?&]v=([\\w-]{11})/);
        if (!m) return;
        const vid = m[1];
        if (seen.has(vid)) return;
        seen.add(vid);
        // 标题优先用 a 的 title 属性（真实视频标题），退化到文本
        let t = (a.getAttribute('title') || a.title || '').trim();
        if (!t) t = (a.textContent || '').trim();
        // 作者：卡片内的频道名容器
        let author = '';
        const ch = card.querySelector('ytd-channel-name, yt-content-metadata-view-model, .yt-core-attributed-string[aria-label], #channel-name');
        if (ch) author = (ch.textContent || '').trim().split('\\u00b7')[0].trim();
        out.push({title: t, author, url: href, video_id: vid});
      });
      return JSON.stringify(out);
    })()"""
    items = fetch_json_js(js)
    if not items:
        print("! 未抓到结果（可能被风控/页面结构变化）")
        return []

    # 过滤无效 title（YouTube 有时 placeholder 会带标题属性）
    out = [it for it in items if it.get("title") and len(it.get("title", "").strip()) >= 4]
    print(f"抓取到 {len(out)} 条去重结果")
    return out[:limit]


def main():
    parser = argparse.ArgumentParser(description="YouTube 最新视频采集（CDP 9222）")
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
                STORAGE_AUDIO / f"youtube_{it['video_id']}.mp3",
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
    print(f"\n完成：{len(items)} 条 YouTube 素材已写入 storage/intel/source/")


if __name__ == "__main__":
    main()
