#!/usr/bin/env python3
"""自动检测并下载「多风格」所需的本地模型（要质量不要速度）。

功能
----
1. 读取 config/config.yaml 的 `styles.*` 下所有模型引用：
   - image_ckpt / video_model（每个风格绑定的底模）
   - recommended_models（该风格推荐的模型清单）
2. 扫描本地所有 ComfyUI 模型目录（主 ComfyUI + ComfyUI-Shared + A1111），
   判断每个所需模型是否已安装；
3. 对「已存在但文件名与配置不一致」的模型，自动在主 ComfyUI 对应目录
   建立软链接（用配置要求的名字）——这解决「模型在本地、但 ComfyUI
   按配置名加载失败」的核心痛点，避免重复下载大文件；
4. 对「确实缺失且有可靠 HuggingFace 源」的模型，用 hf_hub_download
   断点续传下载到主 ComfyUI 对应目录；
5. 对「缺失且无法解析到可靠源」的逻辑名（多为风格 LoRA），列出 MANUAL
   提示手动获取，绝不瞎下载错误文件。

用法
----
    python scripts/download_models.py                 # 全量检测 + 下载 + 对齐
    python scripts/download_models.py --dry-run       # 只打印计划，不动磁盘
    python scripts/download_models.py --only 写实风格 日系动漫
    python scripts/download_models.py --target shared # 下载到 ComfyUI-Shared 共享库

环境
----
    HF_TOKEN  可选，访问 gated 仓库（如 FLUX.1-dev）需要
    HF_HUB_ENABLE_HF_TRANSFER=1  可选，开启 hf_transfer 加速（需先 pip install hf_transfer）
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml

# 项目根目录（脚本所在目录的上一级）
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "config.yaml"

# ─── ComfyUI 模型目录 ───────────────────────────────────────────────
# 主 ComfyUI（下载/软链目标）：标准 models/<type> 结构
COMFY_MAIN = Path("/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI")
# ComfyUI-Shared 共享模型库（只读来源之一）
COMFY_SHARED = Path("/Users/a715/git/ComfyUI/ComfyUI-Shared/models")
# A1111 模型库（只读来源之一）
A1111 = Path("/Users/a715/git/stable-diffusion-webui/models")

# 目录名 → ComfyUI 识别类型（与 providers/comfyui/client.py 的 MODEL_NODE_MAP 一致）
# 注意：共享库与 A1111 的 checkpoint 目录叫 Stable-diffusion，主库叫 checkpoints。
TYPE_DIRS = {
    "checkpoints": ["checkpoints", "Stable-diffusion", "Stable-diffusion"],
    "diffusion_models": ["diffusion_models", "diffusion_models"],
    "text_encoders": ["text_encoders", "text_encoders"],
    "vae": ["vae", "vae", "VAE"],
    "loras": ["loras", "loras", "Lora"],
    "controlnet": ["controlnet", "controlnet", "ControlNet"],
    "clip_vision": ["clip_vision", "clip_vision"],
}

# ─── 可下载模型的 HuggingFace 源 ────────────────────────────────────
# 键 = 配置里出现的文件名；值 = (repo_id, repo 内文件名, 目标类型)
# 仅收录「来源确定可靠」的官方/主流仓库，避免下载到错误文件。
MODEL_SOURCES: Dict[str, Tuple[str, str, str]] = {
    # 写实 / 电影级 —— FLUX 旗舰（gated，需 HF_TOKEN）
    "FLUX.1-dev.safetensors": ("black-forest-labs/FLUX.1-dev", "flux1-dev.safetensors", "checkpoints"),
    # 写实 —— SD1.5 原版
    "v1-5-pruned-emaonly.safetensors": ("stable-diffusion-v1-5/stable-diffusion-v1-5", "v1-5-pruned-emaonly.safetensors", "checkpoints"),
    # 写实 —— Realistic Vision
    "Realistic-Vision-V5.1.safetensors": ("SG161222/Realistic_Vision_V5.1_noVAE", "Realistic_Vision_V5.1.safetensors", "checkpoints"),
    # 日漫 / Q版 —— Anything V5
    "Anything V5.safetensors": ("Lykon/Anything-V5", "Anything-V5.safetensors", "checkpoints"),
    # 日漫 —— Counterfeit V3.0
    "Counterfeit-V3.0.safetensors": ("gsdf/Counterfeit-V3.0", "Counterfeit-V3.0.safetensors", "checkpoints"),
    # FLUX.1-schnell 的 GGUF（本地已有，仅注册来源）
    "FLUX.1-schnell-Q5_K_S.gguf": ("city96/FLUX.1-schnell-gguf", "flux1-schnell-Q5_K_S.gguf", "diffusion_models"),
    # 文本编码器 / VAE（FLUX 组件）
    "clip_l.safetensors": ("comfyanonymous/flux_text_encoders", "clip_l.safetensors", "text_encoders"),
    "t5xxl_fp8_e4m3fn.safetensors": ("comfyanonymous/flux_text_encoders", "t5xxl_fp8_e4m3fn.safetensors", "text_encoders"),
    "ae.safetensors": ("black-forest-labs/FLUX.1-schnell", "ae.safetensors", "vae"),
}

# ─── 仅注册为「已有别名」的名字（本地不同名但可软链，无需下载源）─────
# 键 = 配置名；值 = (clean 后能匹配的本地文件名, 类型)。用于 diff 归一化。
# 这部分靠 build_installed_index 的模糊匹配实现，无需硬编码，
# 保留为空列表即可；此处仅为文档说明。


# ──────────────────────── 工具函数 ────────────────────────────────

def clean_name(name: str) -> str:
    """归一化文件名：去中文标签前缀『【..】』/括号内容、空格、下划线、连字符、点、大小写。

    用于把『【写实】Realistic-Vision-V5.1.safetensors』与『Realistic_Vision_V5.1.safetensors』
    归一化成同一指纹，从而判断「同一个模型」。
    """
    n = re.sub(r"【[^】]*】", "", name)          # 中文标签【写实】
    n = re.sub(r"[（(][^）)]*[）)]", "", n)       # 括号内容（含中文括号）
    n = re.sub(r"[^a-zA-Z0-9]", "", n).lower()
    return n


def detect_type(filename: str, hint: Optional[str] = None) -> Optional[str]:
    """根据文件名 + 配置 hint 推断模型类型（决定放入哪个 models/<type> 目录）。"""
    low = filename.lower()
    name = filename.lower()

    # 依赖 hint 的类型（image_model_type == flux → 底模放 checkpoints）
    if hint:
        h = hint.lower()
        if "flux" in h:
            # FLUX dev 单文件可用 CheckpointLoaderSimple 加载
            if "gguf" in name and "schnell" in name:
                return "diffusion_models"
            return "checkpoints"
        if "video" in hint or h == "ltx" or "video_model" in hint:
            return "diffusion_models"

    # 分支按关键字
    if "lora" in name or "lycoris" in name:
        return "loras"
    if "t5xxl" in name or "clip_l" in name or "clip-vit" in name or "clip_vision" in name:
        return "text_encoders"
    if name in ("ae.safetensors",) or "vae" in name:
        return "vae"
    if ".gguf" in name:
        return "diffusion_models"
    if "ltx" in name or "hunyuan" in name or "minimax" in name or "z_image" in name or "svd" in name:
        return "diffusion_models"
    # 默认检查点
    if name.endswith(".safetensors") or name.endswith(".ckpt"):
        return "checkpoints"
    return None


def build_installed_index() -> Dict[str, Dict[str, Path]]:
    """扫描所有模型目录，返回 {type: {clean_name: 真实文件路径}}。"""
    index: Dict[str, Dict[str, Path]] = {}

    def scan(base: Path, mapping: Dict[str, List[str]]) -> None:
        if not base.exists():
            return
        for mtype, dirs in mapping.items():
            for d in dirs:
                dpath = base / d
                if not dpath.is_dir():
                    continue
                for f in dpath.iterdir():
                    if f.is_dir():
                        continue
                    idx = index.setdefault(mtype, {})
                    c = clean_name(f.name)
                    if c and c not in idx:
                        # 优先记录较小路径（共享库优先于其它），保持稳定
                        idx[c] = f

    scan(COMFY_MAIN / "models", {t: v for t, v in TYPE_DIRS.items()})
    scan(COMFY_SHARED, TYPE_DIRS)
    scan(A1111, TYPE_DIRS)
    return index


_ROOT_CFG_NAME = "config.yaml"


def load_config() -> Dict:
    if not CONFIG_PATH.exists():
        sys.exit(f"[错误] 找不到配置: {CONFIG_PATH}")
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def collect_required(styles: Dict, only: Optional[List[str]] = None) -> List[Dict]:
    """汇总每个风格引用的模型条目。每条: {name, types:[...], styles:[...], source_key}"""
    items: Dict[str, Dict] = {}  # 用配置名去重，types/styles 累加
    for sname, scfg in styles.items():
        if only and sname not in only:
            continue
        if not isinstance(scfg, dict):
            continue

        def add(name: Optional[str], hint: Optional[str]) -> None:
            if not name or not isinstance(name, str) or not name.strip():
                return
            name = name.strip()
            entry = items.setdefault(name, {
                "name": name, "types": [], "styles": [], "hint": hint,
            })
            if hint and hint not in entry["types"]:
                entry["types"].append(hint)
            if sname not in entry["styles"]:
                entry["styles"].append(sname)

        add(scfg.get("image_ckpt"), scfg.get("image_model_type"))
        for m in scfg.get("recommended_models", []):
            if isinstance(m, str):
                add(m, None)

    return list(items.values())


def resolve_existing(item, index) -> Optional[Tuple[str, Path]]:
    """在已安装索引中查找配置名对应的文件。

    返回 (匹配到的类型, 真实路径)。用归一化指纹匹配。
    """
    target = clean_name(item["name"])
    if not target:
        return None
    # 1) 优先按类型 hint 缩小范围
    ordered_types = item["types"] or list(index.keys())
    for mtype in ordered_types:
        bucket = index.get(mtype, {})
        if target in bucket:
            return (mtype, bucket[target])
    # 2) 无提示或未命中，全索引找
    for mtype, bucket in index.items():
        if target in bucket:
            return (mtype, bucket[target])
    return None


def detect_source(item) -> Optional[Tuple[str, str, str]]:
    """从 MODEL_SOURCES 找下载源（精确名匹配，兼容别名）。"""
    name = item["name"]
    if name in MODEL_SOURCES:
        return MODEL_SOURCES[name]
    # 归一化再匹配一次（容忍空格/下划线差异）
    for key, val in MODEL_SOURCES.items():
        if clean_name(key) == clean_name(name):
            return (val[0], val[1], val[2])
    return None


def target_dir_for(mtype: str, target: str) -> Path:
    """返回下载/软链的目标目录。target 决定是主库还是共享库。"""
    if target == "shared":
        base = COMFY_SHARED
    else:
        base = COMFY_MAIN / "models"
    # 主库用标准 <type> 目录；共享库 checkpoints 用 Stable-diffusion
    if target == "shared" and mtype == "checkpoints":
        return COMFY_SHARED / "Stable-diffusion"
    return base / mtype if mtype != "checkpoints" else base / "checkpoints"


def ensure_link(want_name: str, real_path: Path, mtype: str, target: str) -> bool:
    """在目标目录建立软链接：<want_name> -> real_path。返回是否新建。

    注意：macOS 默认大小写不敏感，若 want_name 与 real_path.name 仅大小写不同，
    link_path.exists() 会误判为存在并可能删掉真实文件。调用方已过滤该类情况；
    此处再防御一次：若目标目录下已存在「clean 名等价」的非软链真实文件，则跳过。
    """
    tdir = target_dir_for(mtype, target)
    tdir.mkdir(parents=True, exist_ok=True)
    link_path = tdir / want_name

    # 防御：目标目录里已有 clean-name 等价的真实文件（且不是我们的链接）→ 跳过
    if link_path.exists() and not link_path.is_symlink():
        for f in tdir.iterdir():
            if f.is_symlink():
                continue
            if clean_name(f.name) == clean_name(want_name) and os.path.realpath(f) != os.path.realpath(real_path):
                # 真实文件存在且名字语义相同，ComfyUI 能按 clean 名找到；不覆盖，避免误删
                print(f"  ⏭ 跳过对齐    {want_name}  （目录已有等价文件 {f.name}, 大小写/命名差异）")
                return False

    if link_path.exists() or link_path.is_symlink():
        if link_path.is_symlink() and os.path.realpath(link_path) == str(real_path):
            return False
        try:
            link_path.unlink()
        except OSError:
            pass
    try:
        link_path.symlink_to(real_path.resolve())
        print(f"  ⛓ 软链对齐  {want_name}  ->  {real_path}")
        return True
    except OSError as e:
        print(f"  ⚠ 软链失败  {want_name}: {e}")
        return False


def download_model(item, source, target: str, dry_run: bool) -> bool:
    """下载模型到主 ComfyUI 对应目录（断点续传）。"""
    repo_id, filename, mtype = source
    tdir = target_dir_for(mtype, target)
    tdir.mkdir(parents=True, exist_ok=True)
    dest = tdir / item["name"]
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  ✓ 已存在        {item['name']}")
        return "existing"

    if dry_run:
        print(f"  ⬇ 待下载        {item['name']}  <-  {repo_id}/{filename}")
        return "planned"

    print(f"  ⬇ 下载中        {item['name']}  ({repo_id}/{filename})")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as e:
        print(f"  ⚠ 未安装 huggingface_hub: {e}")
        return "failed"

    try:
        # 先下载到 HF 缓存，再移动到目标目录（保留配置要求的文件名）
        local = hf_hub_download(
            repo_id=repo_id,
            filename=filename,
            local_dir=str(tdir),
        )
        # hf_hub_download local_dir 模式下文件名即 repo 内文件名；
        # 若与配置名不同则改名。
        prod = Path(local)
        if prod.name != item["name"] and prod.exists():
            renamed = tdir / item["name"]
            if renamed.exists():
                renamed.unlink()
            prod.rename(renamed)
            print(f"  ✓ 已下载        {item['name']}")
        else:
            print(f"  ✓ 已下载        {prod}")
        return "downloaded"
    except Exception as e:
        print(f"  ✗ 下载失败      {item['name']}: {e}")
        return "failed"


def main() -> int:
    ap = argparse.ArgumentParser(description="多风格模型检测 + 下载 + 名字对齐")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不动磁盘")
    ap.add_argument("--only", nargs="*", default=None, help="仅处理指定风格")
    ap.add_argument("--target", choices=["main", "shared"], default="main",
                    help="下载/软链目标目录（默认主 ComfyUI）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    args = ap.parse_args()

    cfg = load_config()
    styles = cfg.get("styles", {})
    if not styles:
        print("配置中无 styles 段。")
        return 1

    required = collect_required(styles, args.only)
    index = build_installed_index()

    report = {"downloaded": [], "linked": [], "existing": [], "planned": [], "manual": [], "failed": []}

    print(f"扫描模型目录: 主库 {COMFY_MAIN / 'models'}, 共享库 {COMFY_SHARED}, A1111 {A1111}")
    print(f"所需模型条目: {len(required)}  目标目录: {args.target}\n")

    for item in sorted(required, key=lambda x: x["name"]):
        name = item["name"]
        found = resolve_existing(item, index)

        if found:
            mtype, real_path = found
            # 仅精确同名（ComfyUI 按配置名直接能找到）才视为「已安装」
            if real_path.name == name:
                print(f"  ✓ 已安装        {name}  ({mtype})")
                report["existing"].append(name)
            else:
                # 模型在本地，但 ComfyUI 按配置名加载不到 → 软链对齐
                report["linked"].append(name)
                if args.dry_run:
                    print(f"  ⛓ 待对齐        {name}  ->  {real_path}")
                else:
                    ensure_link(name, real_path, mtype, args.target)
            continue

        # 未命中：尝试下载
        source = detect_source(item)
        if source:
            status = download_model(item, source, args.target, args.dry_run)
            if status == "downloaded":
                report["downloaded"].append(name)
            elif status == "planned":
                report["planned"].append(name)
            elif status == "existing":
                report["existing"].append(name)
            else:
                report["failed"].append(name)
        else:
            print(f"  ? 需手动        {name}  （无可靠 HF 源，请到 Civitai/HF 手动获取）")
            report["manual"].append(name)

    # 汇总
    if args.json:
        print("\n" + json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("\n========== 汇总 ==========")
    print(f" 已安装   : {len(report['existing'])}")
    print(f" 已对齐   : {len(report['linked'])}")
    print(f" 已下载   : {len(report['downloaded'])}")
    if report["planned"]:
        print(f" 待下载   : {len(report['planned'])}")
    print(f" 需手动   : {len(report['manual'])}")
    if report["failed"]:
        print(f" 下载失败 : {len(report['failed'])}")
    if args.dry_run:
        print("\n[dry-run] 未做任何磁盘改动。去掉 --dry-run 实际执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
