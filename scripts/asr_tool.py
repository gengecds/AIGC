#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文件名：asr_tool.py
作用：基于 FunASR + SenseVoice 的本地语音转录（ASR）工具，中文识别效果好。
为什么用这个方案：抖音「算力炼丹炉」推荐本地部署的语音转录方案就是 FunASR·SenseVoice；
      本机已装 funasr 1.4.4 + modelscope，模型很小（约 230MB），M4 CPU 即可实时转录。
用法：
  python scripts/asr_tool.py <音频文件> [音频文件2 ...]   # 显示文件名分隔线
  python scripts/asr_tool.py -i <音频文件>                # 静默模式，只输出识别文本
输出：识别文本（自动清洗 SenseVoice 的语言/情感标签）。
"""

import sys
import time

# SenseVoice 输出会带上 <|zh|> <|NEUTRAL|> 等标签，清洗时统一去掉
_TAGS = ("<|zh|>", "<|en|>", "<|yue|>", "<|ja|>", "<|ko|>", "<|nospeech|>",
         "<|NEUTRAL|>", "<|HAPPY|>", "<|SAD|>", "<|ANGRY|>", "<|OTHER|>",
         "<|Speech|>", "<|EMO_UNKNOWN|>", "<|withitn|>", "<|woitn|>")


def clean_text(text: str) -> str:
    """去掉 SenseVoice 识别结果中的语言/情感标签，返回纯文本。"""
    for tag in _TAGS:
        text = text.replace(tag, "")
    return text.strip()


def transcribe(path: str, model) -> str:
    """对单个音频文件做语音识别，返回清洗后的识别文本。

    :param path: 音频文件路径（wav/mp3/m4a/flac 等 ffmpeg 能解码的格式）
    :param model: 已加载的 funasr AutoModel 实例（只加载一次，多文件复用）
    """
    print(f"===== {path} =====")
    t0 = time.time()
    # SenseVoice 参数说明：
    #   language="zh"：限定中文（不设则自动检测）
    #   use_itn=True：把"一二三"之类口语数字转写成"123"，更规范
    res = model.generate(input=path, cache={}, language="zh", use_itn=True)
    text = clean_text("".join(r.get("text", "") for r in res))
    print(f"识别文本: {text}")
    print(f"耗时: {time.time()-t0:.1f}s")
    return text


def main():
    # 解析参数：-i 表示静默模式（不显示文件名分隔线），方便脚本调用时直接取纯文本
    args = sys.argv[1:]
    quiet = bool(args and args[0] == "-i")
    if quiet:
        args = args[1:]

    if not args:
        print("用法: python scripts/asr_tool.py [-i] <音频文件> [音频文件2 ...]")
        sys.exit(1)

    # 加载模型（SenseVoiceSmall 已下载到本机缓存，秒级加载）
    from funasr import AutoModel
    print("加载 SenseVoiceSmall 模型 ...")
    model = AutoModel(
        model="iic/SenseVoiceSmall",
        trust_remote_code=True,
        disable_update=True,
    )

    # 逐个文件转录；静默模式只打印识别文本，普通模式带分隔线和耗时
    for path in args:
        if quiet:
            res = model.generate(input=path, cache={}, language="zh", use_itn=True)
            print(clean_text("".join(r.get("text", "") for r in res)))
        else:
            transcribe(path, model)


if __name__ == "__main__":
    main()
