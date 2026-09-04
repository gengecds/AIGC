#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：postprocess_platform_videos.py
作用：多平台素材的 ASR 转写 —— 复用 AIGC 自有 scripts/asr_tool.py（FunASR + SenseVoice，
      中文识别效果好），把已下载的音频转成逐字稿，写回 storage/intel/source/*.json 的
      transcript 字段，供 IntelAgent 作爆款拆解。

只处理「有 video_file 且本地音频存在」的素材；已有 transcript 的默认跳过（--force 可重转）。

用法：
    python scripts/intel/postprocess_platform_videos.py                 # 转写全部待处理素材
    python scripts/intel/postprocess_platform_videos.py --force         # 忽略已有 transcript，全部重转
    python scripts/intel/postprocess_platform_videos.py --limit 5       # 最多转 5 条
输出：
    storage/intel/source/*.json   # 回填 transcript 字段
"""

import argparse
import json
import sys
import time
from pathlib import Path

# 项目根 + scripts/（asr_tool.py 是顶层脚本，需把 scripts 加入 sys.path 才能 import）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from asr_tool import transcribe  # noqa: E402  (FunASR + SenseVoice)

SOURCE_DIR = _PROJECT_ROOT / "storage" / "intel" / "source"


def load_model():
    """加载 SenseVoiceSmall（只加载一次，多文件复用）。"""
    from funasr import AutoModel
    print("加载 SenseVoiceSmall 模型 + VAD 分段 ...")
    # vad_model：超长音频（如 30-70 分钟的 YouTube 视频）一次性喂入会导致推理卡死，
    #            加 VAD 把长音频切成短语音段（最长 30s/段），避免整段送入模型。
    return AutoModel(
        model="iic/SenseVoiceSmall",
        vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        vad_kwargs={"max_single_segment_time": 30000},
        trust_remote_code=True,
        disable_update=True,
        disable_pbar=True,
    )


def pick_targets(force: bool, limit: int) -> list[tuple[Path, dict, Path]]:
    """挑出需要转写的素材：有 video_file、音频文件存在、（force 或 无 transcript）。"""
    if not SOURCE_DIR.exists():
        print("! storage/intel/source/ 目录为空，无素材可转写")
        return []

    targets = []
    for p in sorted(SOURCE_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! 跳过 {p}: 解析失败 {e}")
            continue

        vf = d.get("video_file", "")
        if not vf:
            continue
        audio = Path(vf)
        if not audio.exists():
            print(f"  ! 跳过 {p}: 音频不存在 {audio}")
            continue
        if not force and d.get("transcript"):
            print(f"  = 跳过 {p}: 已有 transcript")
            continue
        targets.append((p, d, audio))

    targets.sort(key=lambda t: t[0].name)
    return targets[:limit] if limit > 0 else targets


def main():
    parser = argparse.ArgumentParser(description="多平台素材 ASR 转写（复用 asr_tool FunASR）")
    parser.add_argument("--force", action="store_true", help="忽略已有 transcript，全部重转")
    parser.add_argument("--limit", type=int, default=0, help="最多转写条数（0=不限）")
    args = parser.parse_args()

    targets = pick_targets(args.force, args.limit)
    if not targets:
        print("没有需要转写的素材（可先运行 download_*_latest.py --with-audio 下载音频）")
        sys.exit(0)

    model = load_model()
    ok = 0
    for p, d, audio in targets:
        print(f"\n===== {p.name} =====")
        try:
            text = transcribe(str(audio), model)
        except Exception as e:
            print(f"  ! 转写失败 {audio}: {e}")
            continue
        d["transcript"] = text
        # 校验 video_meta 是否完整，缺平台名则补兜底
        d.setdefault("video_meta", {})
        d["video_meta"].setdefault("platform", "未知")
        d["video_meta"].setdefault("title", d["video_meta"].get("title") or audio.stem)
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        ok += 1
        print(f"  ✓ 已回填 transcript（{len(text)} 字）→ {p}")

    print(f"\n转写完成：成功 {ok} 条 / 共 {len(targets)} 条")


if __name__ == "__main__":
    main()
