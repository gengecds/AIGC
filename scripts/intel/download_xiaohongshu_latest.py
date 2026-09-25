#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：download_xiaohongshu_latest.py
作用：小红书最新笔记采集 —— 用本地 Chrome（CDP 9222）按关键词搜索，抓取笔记详情，
      可选下载音频（yt-dlp 优先 / 页面媒体 URL 降级），落盘到 storage/intel/source/*.json，
      供 IntelAgent 作爆款素材源。

为什么用本地 Chrome + CDP：内容平台对无头浏览器有风控，见 collect_materials.py 说明。

用法：
    python scripts/intel/download_xiaohongshu_latest.py "穿搭"                 # 只采集详情
    python scripts/intel/download_xiaohongshu_latest.py "穿搭" --with-audio    # 同时下载音频（前3条）
    python scripts/intel/download_xiaohongshu_latest.py "穿搭" --limit 12      # 最多抓 12 条
输出：
    storage/intel/source/xiaohongshu_<id>_<seq>.json   # 每笔记一个文件（IntelAgent 读取）
    storage/intel/audio/*                              # 已下载音频（若 --with-audio）
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

PLATFORM = "小红书"
_NOTE_ID_RE = r"/(?:explore|discovery/item)/([0-9a-f]+)"


def collect(keyword: str, limit: int) -> list[dict]:
    """按关键词搜索小红书，抓取笔记条目（title / url / note_id）。"""
    url = f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}"
    print("打开小红书搜索页:", ab("open", url))
    time.sleep(6)
    deep_scroll(3, 2)

    # 小红书笔记卡片：链接 href 含 /explore/<id> 或 /discovery/item/<id>。注意卡片 <a> 是纯图片、
    # 无 title 属性和文本，真实标题在卡片容器内的 [class*="title"] 元素里，须从容器往上取。
    js = """(()=>{
      const seen = new Set();
      const out = [];
      document.querySelectorAll('a[href*="/explore/"], a[href*="/discovery/item/"]').forEach(a => {
        const href = a.getAttribute('href') || '';
        const m = href.match(/\\/(?:explore|discovery\\/item)\\/([0-9a-f]+)/);
        if (!m) return;
        const id = m[1];
        const card = a.closest('section') || a.closest('div');
        let title = '';
        if (card) {
          const te = card.querySelector('[class*="title"] span, [class*="title"]');
          title = te ? (te.textContent || '').replace(/\\s+/g, ' ').trim() : '';
        }
        if (!title) {  // 兜底：沿父级向上找最近的标题容器
          let n = a.parentElement;
          for (let i = 0; i < 6 && n && !title; i++) {
            const te = n.querySelector('[class*="title"]');
            if (te) title = (te.textContent || '').replace(/\\s+/g, ' ').trim();
            n = n.parentElement;
          }
        }
        if (title.length >= 4 && !seen.has(id)) {
          seen.add(id);
          out.push({title, url: a.href, video_id: id});
        }
      });
      return JSON.stringify(out);
    })()"""
    items = fetch_json_js(js)
    if not items:
        print("! 未抓到结果（可能被风控/页面结构变化）")
        return []

    out = []
    for it in items:
        vid = extract_video_id(it["url"], _NOTE_ID_RE)
        if not vid:
            continue
        out.append({"title": it["title"], "url": it["url"], "video_id": vid})
    seen = set()
    dedup = []
    for it in out:
        if it["video_id"] in seen:
            continue
        seen.add(it["video_id"])
        dedup.append(it)
    print(f"抓取到 {len(dedup)} 条去重结果")
    return dedup[:limit]


def main():
    parser = argparse.ArgumentParser(description="小红书最新笔记采集（CDP 9222）")
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
                STORAGE_AUDIO / f"xiaohongshu_{it['video_id']}.mp3",
                referer=it["url"],
            )
        dest = save_source(
            title=it["title"],
            creator_name="",
            platform=PLATFORM,
            url=it["url"],
            video_id=it["video_id"],
            video_file=str(audio_path) if audio_path else "",
        )
        print(f"  ✓ 落盘: {dest}")
    print(f"\n完成：{len(items)} 条小红书素材已写入 storage/intel/source/")


if __name__ == "__main__":
    main()
