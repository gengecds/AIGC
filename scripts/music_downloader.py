"""无损音乐 + 歌词下载器（agent-browser 驱动版）

用 agent-browser（免费开源，Vercel Labs）连接本地 Chrome（CDP 9222），
从 flac.music.hi.cn 搜索并下载 BGM 与 LRC 歌词。

依赖：npm install -g agent-browser（已装 v0.35.0）
用法：python scripts/music_downloader.py "温馨钢琴曲" [输出目录]
输出到 storage/music/，音乐重命名带情绪关键词，管线按情绪自动选用。
"""
import subprocess
import sys
import time
from pathlib import Path

MUSIC_DIR = Path(__file__).parent.parent / "storage" / "music"
SITE = "https://flac.music.hi.cn/"
AUDIO_SUFFIX = (".mp3", ".flac", ".wav", ".m4a", ".ogg")


def ab(*args: str) -> str:
    """执行 agent-browser --cdp 9222 命令，返回 stdout"""
    r = subprocess.run(
        ["agent-browser", "--cdp", "9222", *args],
        capture_output=True, text=True, timeout=60,
    )
    return (r.stdout or r.stderr or "").strip()


def eval_js(js: str) -> str:
    """执行页面内 JS，返回结果"""
    return ab("eval", js)


def wait_new_file(out_dir: Path, before: set, timeout=150) -> Path | None:
    """轮询输出目录等新文件下载完成（浏览器直接落盘，文件名多为乱码无扩展名）

    用文件头判断类型：FLAC=fLaC / MP3=ID3 / LRC=[ti: 。超过 150s 视为超时。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(4)
        if not out_dir.exists():
            continue
        for f in out_dir.iterdir():
            if f.name in before or f.name.endswith(".crdownload"):
                continue
            # 只认"下载完成"的文件（>1KB，且头部可识别）
            if f.stat().st_size < 1024:
                continue
            head = f.read_bytes()[:4]
            if head == b"fLaC" or head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3") or head[:4] == b"[ti:":
                return f
    return None


def detect_and_rename(f: Path, keyword: str) -> str:
    """按文件头识别类型并重命名（音乐带情绪关键词，歌词带"歌词_"前缀）"""
    head = f.read_bytes()[:4]
    if head == b"fLaC":
        suffix = ".flac"
    elif head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3"):
        suffix = ".mp3"
    elif head[:4] == b"[ti:":
        suffix = ".lrc"
    else:
        suffix = f.suffix or ".bin"
    base = Path(f.name).stem
    if suffix == ".lrc":
        dst = f.parent / f"歌词_{keyword}_{base}.lrc"
    else:
        dst = f.parent / f"{keyword}_{base}{suffix}"
    f.rename(dst)
    return str(dst)


def main(keyword: str, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. 打开网站（CDN 风控时自动等待重试：防护页无输入框）
    import json as _json
    ok_page = False
    for attempt in range(1, 4):
        print(f"打开音乐站(第{attempt}次):", ab("open", SITE))
        time.sleep(6)
        st = eval_js("(()=>{const i=document.querySelector('input'); return i ? 'ok' : 'blocked'})()")
        if st == "ok":
            ok_page = True
            break
        print("⏳ 触发 CDN 安全验证，等待 5 分钟后重试…")
        time.sleep(300)
    if not ok_page:
        print("✗ 多次尝试仍被 CDN 防护拦截，请稍后再试")
        return

    # 2. 输入关键词 + 搜索（eval 定位，不依赖会变的 ref）
    eval_js(f"""(()=>{{
      const i = Array.from(document.querySelectorAll('input')).find(x => x.placeholder.includes('搜索'));
      if (!i) return 'no input';
      const s = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      s.call(i, {_json.dumps(keyword)});
      i.dispatchEvent(new Event('input', {{bubbles: true}}));
      return 'typed';
    }})()""")
    time.sleep(1)
    print("搜索:", eval_js("""(()=>{
      const b = Array.from(document.querySelectorAll('button')).find(x => /搜索/.test(x.textContent || ''));
      if (b) { b.click(); return 'clicked'; }
      return 'no btn';
    })()"""))
    time.sleep(8)

    # 3. 关闭可能残留的下载弹窗，再打开第一个结果的下载弹窗
    print("清理弹窗:", eval_js("""(()=>{
      const c = document.querySelector('.ant-modal-close');
      if (c) { c.click(); return 'closed'; }
      return 'none';
    })()"""))
    time.sleep(2)
    print("下载弹窗:", eval_js("""(()=>{
      const btn = Array.from(document.querySelectorAll('button[aria-label="下载"]'))[0];
      if (btn) { btn.click(); return 'clicked'; }
      return 'no dl btn';
    })()"""))
    time.sleep(4)

    before = set(f.name for f in out_dir.iterdir())

    # 4. 下载无损 FLAC
    print("选择FLAC:", eval_js("""(()=>{
      const btn = document.querySelector('.download-options button');
      if (btn) { btn.click(); return 'clicked'; }
      return 'no option';
    })()"""))
    music_file = wait_new_file(out_dir, before, timeout=150)
    if music_file:
        dst = detect_and_rename(music_file, keyword)
        sz = Path(dst).stat().st_size / 1024 / 1024
        print(f"✓ 音乐已保存: {Path(dst).name} ({sz:.1f}MB)")
    else:
        print("✗ 音乐下载超时（该曲目可能无 FLAC 源），跳过歌词")
        print("音乐库文件:", [f.name for f in out_dir.iterdir()])
        return  # 没有音乐就无需再找歌词

    # 5. 下载歌词 LRC（先关掉音乐弹窗，重新打开选"歌词文件"）
    #    歌词为可选项：获取不到直接跳过，不阻塞主流程
    print("关弹窗:", eval_js("""(()=>{
      const c = document.querySelector('.ant-modal-close');
      if (c) { c.click(); return 'closed'; }
      return 'none';
    })()"""))
    time.sleep(2)
    print("重新打开弹窗:", eval_js("""(()=>{
      const btn = Array.from(document.querySelectorAll('button[aria-label="下载"]'))[0];
      if (btn) { btn.click(); return 'clicked'; }
      return 'no btn';
    })()"""))
    time.sleep(4)
    print("选择LRC:", eval_js("""(()=>{
      const bs = document.querySelectorAll('.download-options button');
      if (bs.length) { bs[bs.length - 1].click(); return 'clicked ' + bs.length; }
      return 'none';
    })()"""))
    before_lrc = set(f.name for f in out_dir.iterdir())
    # 歌词快速尝试 25s，拿不到就跳过（纯音乐/无歌词曲目常见）
    lrc_file = wait_new_file(out_dir, before_lrc, timeout=25)
    if lrc_file:
        dst_lrc = detect_and_rename(lrc_file, keyword)
        print(f"✓ 歌词已保存: {Path(dst_lrc).name}")
    else:
        print("— 未获取到歌词，已跳过（纯音乐/无歌词曲目）")

    print("音乐库文件:", [f.name for f in out_dir.iterdir()])


if __name__ == "__main__":
    import json
    kw = sys.argv[1] if len(sys.argv) > 1 else "温馨钢琴曲"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else MUSIC_DIR
    main(kw, out)
