#!/usr/bin/env python3
"""生产版本：全量端到端运行 (2集全29分镜 → 出图 → 视频 → 字幕 → 回传 → MP4)

用户已睡觉，全自动通宵跑。早上去验收。
"""
import sys, os, asyncio, json, subprocess, logging, time as ttime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import site
site.addsitedir("/Users/mac/Library/Python/3.14/lib/python/site-packages")

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
from pathlib import Path
BASE = Path(__file__).parent
OUT = BASE / "storage" / "output"
OUT.mkdir(parents=True, exist_ok=True)
CHECK = BASE / "storage" / "checkpoints_real"
CHECK.mkdir(parents=True, exist_ok=True)

SSH_PW = "900917_19871002-Gz"
LOCAL_PORT = 18188

def log(msg):
    print(f"[{ttime.strftime('%H:%M:%S')}] {msg}", flush=True)

def ensure_tunnel():
    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)
    r = subprocess.run(["sshpass","-p",SSH_PW,"ssh","-o","StrictHostKeyChecking=no",
        "-p","30476","-L",f"{LOCAL_PORT}:localhost:8188","-f","-N",
        "root@connect.bjb2.seetacloud.com"], capture_output=True, text=True)
    return r.returncode == 0 or "already" in r.stderr

async def main():
    from providers.comfyui.client import ComfyUIClient
    from providers.result_fetcher import ResultFetcher
    from agents.script_agent import ScriptAgent
    from agents.storyboard_agent import StoryboardAgent
    from agents.character_agent import CharacterDesignAgent
    from agents.image_agent import ImageGenAgent
    from agents.video_agent import VideoGenAgent
    from agents.subtitle_agent import SubtitleAgent
    from agents.compose_agent import ComposeAgent

    log("🚀 隧道检查...")
    if not ensure_tunnel():
        log("❌ 隧道失败"); return
    comfy = ComfyUIClient(server_addr="127.0.0.1", server_port=LOCAL_PORT)

    # ── 1/7 剧本 ──────────────────────────
    log("📝 1/7 剧本生成...")
    r1 = await ScriptAgent(llm_provider="deepseek").run(
        "末世题材，主角林峰在丧尸爆发后独自生存")
    log(f"    ✅ {r1.data['title']} | {len(r1.data['episodes'])}集 | {len(r1.data['characters'])}角色")
    json.dump(r1.data, open(CHECK/"script_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 2/7 分镜 ──────────────────────────
    log("🎬 2/7 分镜生成...")
    r2 = await StoryboardAgent(llm_provider="deepseek").run(r1.data)
    shots_total = sum(len(e.get("shots",[])) for e in r2.data["episodes"])
    log(f"    ✅ {shots_total}个分镜")
    json.dump(r2.data, open(CHECK/"storyboard_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 3/7 定妆照 ──────────────────────────
    log("🎨 3/7 角色定妆照（ComfyUI）...")
    r3 = await CharacterDesignAgent(use_comfyui=True, comfy_client=comfy).run(r1.data)
    log(f"    ✅ {r3.metadata['generated']}角色")
    json.dump(r3.data, open(CHECK/"character_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 4/7 SD出图 ──────────────────────────
    log("🖼️ 4/7 SD批量出图（ComfyUI并行）...")
    img = ImageGenAgent(use_comfyui=True)
    img.image_provider.client = comfy
    r4 = await img.run(r2.data)
    total_imgs = sum(len(v or []) for v in r4.data["images"].values())
    log(f"    ✅ {total_imgs}张分镜图")
    json.dump(r4.data, open(CHECK/"image_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 5/7 图生视频 ──────────────────────────
    log("🎥 5/7 HunyuanVideo图生视频...")
    vid = VideoGenAgent(use_comfyui=True, comfy_client=comfy)
    r5 = await vid.run(r4)
    total_vids = sum(len(v or []) for v in r5.data["videos"].values())
    log(f"    ✅ {total_vids}段视频")
    json.dump(r5.data, open(CHECK/"video_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 6/7 字幕 ──────────────────────────
    log("📄 6/7 字幕生成...")
    r6 = await SubtitleAgent().run(r1.data, r2.data, output_dir=str(OUT))
    log(f"    ✅ {r6.metadata['files']}个SRT")
    json.dump(r6.data, open(CHECK/"subtitle_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)

    # ── 7/7 合成MP4 ──────────────────────────
    if total_vids > 0:
        log("🎬 7/7 MP4合成...")
        r7 = await ComposeAgent().run(r5, r6)
        log(f"    ✅ MP4产出")
        json.dump(r7.data, open(CHECK/"compose_agent_checkpoint.json","w"), ensure_ascii=False, indent=2)
    else:
        log("⚠️  7/7 无视频段，跳过合成")

    # ── 回传 ─────────────────────────────
    log("📡 回传GPU产出...")
    try:
        fetcher = ResultFetcher(server_addr="127.0.0.1", server_port=LOCAL_PORT)
        all_results = {}
        for r in [r3, r4, r5]:
            if hasattr(r, "data"):
                all_results.update(r.data)
        fetched = await fetcher.fetch_results(all_results)
        total_files = sum(len(v) for v in fetched.values())
        log(f"    ✅ 回传 {total_files} 个文件到 {OUT}")
    except Exception as e:
        log(f"    ⚠️ 回传失败: {e}")

    await comfy.close()
    subprocess.run(f"kill $(lsof -t -i :{LOCAL_PORT}) 2>/dev/null", shell=True)

    # ── 最终报告 ───────────────────────────
    log("="*60)
    log(f"✅ 全量生产完成！")
    log(f"   剧名: {r1.data['title']}")
    log(f"   分镜: {shots_total} → 出图 {total_imgs} → 视频 {total_vids}")
    log(f"   文件存于: {OUT}")
    log("="*60)

if __name__ == "__main__":
    asyncio.run(main())
