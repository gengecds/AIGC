#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：common.py
作用：素材源补齐的公共模块 —— 抖音/小红书/YouTube 采集脚本共享的底层能力：
      1. CDP 抓取：agent-browser --cdp 9222 连接本机真实 Chrome（禁止 headless / 独立 Chromium，
         内容平台对无头浏览器有风控 —— 见 collect_materials.py 的全局规则说明）；
      2. 音频下载：优先 yt-dlp（若安装），否则尝试解析页面媒体 URL 降级；
      3. 落盘：把采集到的「作品详情 + 转录」写入 storage/intel/source/*.json，
         格式与 agents/intel_agent.py 的 _to_video_meta/_to_transcript 完全对接。

零破坏红线（见方案文档 §7.1）：本目录只新增文件，不修改 AIGC 现有任何代码。
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# 项目根路径：scripts/intel/common.py → 向上 3 级
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

STORAGE_SOURCE = _PROJECT_ROOT / "storage" / "intel" / "source"

# 下载音频的临时落地目录（转写脚本会消费它，最终只把 transcript 落回 source/*.json）
STORAGE_AUDIO = _PROJECT_ROOT / "storage" / "intel" / "audio"

# yt-dlp 需要登录态 cookie（抖音/YouTube 有 bot 校验），从 CDP 会话导出成 Netscape 文件
_COOKIES_FILE = STORAGE_AUDIO / "cookies.txt"


# ── CDP 基础能力（连接本机真实 Chrome）────────────────────
def ab(*args: str) -> str:
    """执行 agent-browser --cdp 9222 命令，返回 stdout（连接本地真实 Chrome）。"""
    r = subprocess.run(
        ["agent-browser", "--cdp", "9222", *args],
        capture_output=True, text=True, timeout=60,
    )
    return (r.stdout or r.stderr or "").strip()


def eval_js(js: str) -> str:
    """在页面里执行 JS，返回结果字符串。"""
    return ab("eval", js)


def deep_scroll(times: int = 3, pause: float = 2.0) -> None:
    """滚动页面触发懒加载，拿到更多结果。"""
    for _ in range(times):
        eval_js("window.scrollTo(0, document.body.scrollHeight); 'scrolled'")
        time.sleep(pause)


def fetch_json_js(js: str) -> list[Any]:
    """执行一段返回 JSON 数组的 JS。

    注意：agent-browser 的 eval 会把 JS 返回值再 JSON.stringify 一次（返回带引号的
    JSON 字符串），因此这里做双层解析：第一次得到字符串则再解析一次。
    解析失败返回空列表。
    """
    try:
        raw = eval_js(js)
        if not raw:
            return []
        d = json.loads(raw)
        if isinstance(d, str):  # 被 agent-browser 二次序列化 → 再解一层
            d = json.loads(d)
        return d if isinstance(d, list) else []
    except Exception as e:
        print(f"  ! JS 抓取失败: {e}")
        return []


# ── 清洗工具 ─────────────────────────
def clean_dir_name(text: str) -> str:
    """把任意字符串清洗成安全文件名（保留中英文、数字、-、_）。"""
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", text).strip("_") or "item"


def extract_video_id(url: str, pattern: str) -> str:
    """从链接里抽作品 ID（如视频 ID / 笔记 ID）。"""
    m = re.search(pattern, url)
    return m.group(1) if m else ""


# ── 音频下载：yt-dlp 优先 → 页面媒体 URL 降级 ─────────────
def has_ytdlp() -> bool:
    return shutil.which("yt-dlp") is not None


def _fetch(url: str, out_path: Path, referer: str = "") -> bool:
    """用 urllib 下载 URL（带 UA / Referer），供降级方案使用。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/126.0 Safari/537.36"),
    })
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=60) as resp:
        out_path.write_bytes(resp.read())
    return out_path.exists() and out_path.stat().st_size > 0


def _grab_page_media_url() -> str:
    """从当前 CDP 页面 DOM 探测可下载的视频/音频源。"""
    js = """(()=>{
      const v = document.querySelector('video');
      if (v) {
        const s = v.src || (v.querySelector('source') && v.querySelector('source').src || '');
        if (s) return s;
        if (v.currentSrc) return v.currentSrc;
      }
      const og = document.querySelector('meta[property="og:video"]')
              || document.querySelector('meta[property="og:video:url"]')
              || document.querySelector('meta[itemprop="contentUrl"]');
      if (og) return (og.content || og.getAttribute('content') || '');
      return '';
    })()"""
    return eval_js(js)


def _registrable_root(host: str) -> str:
    """从 host（如 www.douyin.com / m.xiaohongshu.com）取「注册域根」（douyin.com）。

    只针对本场景的常见 TLD（.com/.org/.net/.cn 等单层 TLD）；不处理 com.cn 这类双层 TLD。
    """
    parts = [p for p in host.lower().strip(".").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def export_cookies(netscape_path: Path = _COOKIES_FILE, page_url: str = "") -> bool:
    """把当前 CDP Chrome 会话的 cookies 导出为 yt-dlp 可读的 Netscape cookies.txt。

    源站（抖音/YouTube 等）有 bot 校验，yt-dlp 直接下载会被要求登录；而本地 Chrome
    的登录态就存在 CDP 会话里，这里用 `agent-browser cookies get --json` 取出来转成
    Netscape 格式，供 yt-dlp --cookies 使用。

    关键：CDP 会话是整个 Chrome 共享的，会累积多个站点（douyin/youtube/xiaohongshu/...）
    的 cookies。yt-dlp 解析时若文件里混入其他站点的 cookie，整个文件会被判为
    "invalid Netscape format"。因此这里按 page_url 的主机做「注册域」过滤，只保留目标
    站点域名的 cookie，并跳过 name/value 为空、格式异常的条目。导出成功（至少 1 条）返回 True。
    """
    r = ab("cookies", "get", "--json")
    try:
        d = json.loads(r)
    except Exception:
        return False
    cookies = ((d or {}).get("data") or {}).get("cookies") or []
    if not cookies:
        return False

    # 目标站点的注册域，如 douyin.com；未提供 page_url 则不过滤
    if page_url:
        try:
            host = urllib.parse.urlparse(page_url).hostname or ""
        except Exception:
            host = ""
        root = _registrable_root(host) if host else ""
    else:
        root = ""

    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = (c.get("domain") or "").strip()
        name = (c.get("name") or "").strip()
        if not domain or not name:
            continue
        # 域名过滤：只保留目标站点（域名去点后等于根，或其后缀为 .根）
        if root:
            dom_trim = domain.lower().lstrip(".")
            if not (dom_trim == root or dom_trim.endswith("." + root)):
                continue
        # strip 后字段里若含 \t 会让 Netscape 解析撕裂（值变多字段），直接跳过该 cookie
        raw = c.get("value") or ""
        value = raw.replace("\n", " ").replace("\r", " ").strip()
        if not value or "\t" in value or "\t" in domain or "\t" in name or "\t" in (c.get("path") or ""):
            continue
        path = (c.get("path") or "/").strip() or "/"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expiry = max(int(c.get("expires") or 0), 0)
        # Mozilla/Netscape 解析断言：第 2 列 domain_specified 必须等于「域名是否带前导点」。
        # 域名以 . 开头 → TRUE（domain cookie）；不带点 → FALSE（host-only cookie）。
        domain_specified = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append("\t".join([domain, domain_specified, path, secure, str(expiry), name, value]))

    if len(lines) == 1:  # 只有表头，没有可用 cookie
        return False
    netscape_path.parent.mkdir(parents=True, exist_ok=True)
    netscape_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def download_audio(page_url: str, audio_path: Path, referer: str = "") -> Path | None:
    """下载一个作品的音频。优先 yt-dlp，失败则用 CDP 打开页面解析媒体 URL 降级。

    返回音频文件路径；失败（两种方式都不可用/下载失败）返回 None，不影响详情落盘。
    """
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. 优先 yt-dlp（若安装）
    if has_ytdlp():
        try:
            # 先打开作品页，让 CDP 会话持有该源站的登录态 cookie，再导出给 yt-dlp
            ab("open", page_url)
            time.sleep(3)
            export_cookies(page_url=page_url)
            # YouTube 需要 JS runtime 求解 EJS 挑战（本机已有 node），故显式启用 node 并拉取 ejs 组件；
            # 缺这些时 yt-dlp 会报 "No supported JavaScript runtime" / "n challenge solving failed"
            cmd = ["yt-dlp", "--js-runtimes", "node", "--remote-components", "ejs:github",
                   "-f", "bestaudio/best", "-o", str(audio_path)]
            if _COOKIES_FILE.exists():
                cmd += ["--cookies", str(_COOKIES_FILE)]
            # 缺 JS runtime 时降低严格度，避免部分 format 缺失报错
            cmd += ["--no-check-formats", page_url]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 0:
                print(f"  ✓ yt-dlp 下载音频: {audio_path}")
                return audio_path
            print(f"  ! yt-dlp 下载失败: {(r.stderr or r.stdout or '').strip().splitlines()[-1] if (r.stderr or r.stdout) else ''}")
            print("    尝试页面媒体 URL 降级")
        except Exception as e:
            print(f"  ! yt-dlp 异常: {e}")
    else:
        print("  ! yt-dlp 未安装，尝试页面媒体 URL 降级")

    # 2. 降级：CDP 打开页面，抓取 <video>/og:video 源
    try:
        print("  打开作品页:", ab("open", page_url))
        time.sleep(4)
        deep_scroll(1, 1.5)
        media = _grab_page_media_url()
        if media and _fetch(media, audio_path, referer=referer or page_url):
            print(f"  ✓ 页面媒体 URL 降级下载: {audio_path}")
            return audio_path
        print("  ! 未解析到可下载的媒体 URL（跳过音频）")
    except Exception as e:
        print(f"  ! 降级下载异常: {e}")
    return None


# ── 落盘到 storage/intel/source/ ─────────────
def ensure_dirs() -> tuple[Path, Path]:
    STORAGE_SOURCE.mkdir(parents=True, exist_ok=True)
    STORAGE_AUDIO.mkdir(parents=True, exist_ok=True)
    return STORAGE_SOURCE, STORAGE_AUDIO


def save_source(
    title: str,
    creator_name: str,
    platform: str,
    url: str,
    video_id: str,
    transcript: str = "",
    duration_seconds: int = 0,
    video_file: str = "",
    extra: dict | None = None,
) -> Path:
    """把单个作品落盘为 storage/intel/source/*.json（与 IntelAgent 读取格式对接）。

    覆盖字段：video_meta（title/creator_name/platform/duration_seconds/url/video_id）
             + transcript + video_file（本地音频路径）。
    """
    STORAGE_SOURCE.mkdir(parents=True, exist_ok=True)

    slug = clean_dir_name(title)
    seq = int(time.time() * 1000) % 100000
    dest = STORAGE_SOURCE / f"{platform}_{video_id or slug}_{seq}.json"

    meta = {
        "title": title,
        "creator_name": creator_name,
        "platform": platform,
        "duration_seconds": duration_seconds,
        "url": url,
        "video_id": video_id,
    }
    data = {"video_meta": meta, "transcript": transcript}
    if video_file:
        data["video_file"] = video_file
    if extra:
        data.update(extra)

    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
