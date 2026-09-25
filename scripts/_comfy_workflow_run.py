#!/usr/bin/env python
"""在 ComfyUI 中创建工作流，并在该工作流上跑图（只用系统现有模型）。

流程：
  1) 创建工作流：用 providers/comfyui/workflow_templates 生成画布(UI)格式
     JSON，落盘到 ComfyUI 工作流库 user/default/workflows/，可在网页里看到/加载。
  2) 跑图：用同源的 API 格式工作流 POST /prompt，轮询完成后把图片同步落地到
     storage/output/images。

用法：
    python scripts/_comfy_workflow_run.py                     # 全部模板创建 + 可跑的跑一张
    python scripts/_comfy_workflow_run.py sd15 flux           # 只处理指定模板
    python scripts/_comfy_workflow_run.py sd15 --prompt "a cat" --width 768 --height 768 --steps 25
    python scripts/_comfy_workflow_run.py controlnet --image storage/output/images/xxx.png
    python scripts/_comfy_workflow_run.py ipadapter --image storage/output/images/定妆照.png \
        --preset "PLUS FACE (portraits)"
    python scripts/_comfy_workflow_run.py --create-only        # 只创建工作流，不跑图

可传参：--prompt / --negative / --width / --height / --steps / --cfg / --seed
       / --image（ControlNet、IP-Adapter 的输入图，本地路径）
       / --preset（IP-Adapter 预设，ipadapter / ipadapter_faceid 模板生效）
       / --lora-strength、--provider（仅 ipadapter_faceid 模板生效）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from providers.comfyui.workflow_templates import TEMPLATES, api_workflow, ui_workflow
from providers.comfyui.client import ComfyUIClient
from providers.comfyui_provider import sync_image_to_local

# ComfyUI 桌面版工作流库
COMFY_WF_DIR = Path(
    "/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/user/default/workflows"
)
SERVER_ADDR, SERVER_PORT = "127.0.0.1", 8189

ALL_KINDS = [t["kind"] for t in TEMPLATES]


def _client() -> ComfyUIClient:
    return ComfyUIClient(server_addr=SERVER_ADDR, server_port=SERVER_PORT, timeout=3600)


def create_workflow(kind: str, **overrides) -> Path:
    """生成并保存工作流到 ComfyUI 工作流库，返回文件路径。"""
    filename = next(t["filename"] for t in TEMPLATES if t["kind"] == kind)
    COMFY_WF_DIR.mkdir(parents=True, exist_ok=True)
    path = COMFY_WF_DIR / filename
    path.write_text(
        json.dumps(ui_workflow(kind, **overrides), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[创建] {kind:10s} -> {path}")
    return path


async def _missing_nodes(client: ComfyUIClient, workflow: dict) -> list[str]:
    """返回工作流里本机 ComfyUI 未安装的节点类型。"""
    info = await client.get_object_info()
    used = {n["class_type"] for n in workflow.values()}
    return sorted(used - set(info))


async def _upload_input(image_path: str) -> str:
    """把本地输入图上传到 ComfyUI input/，返回工作流里可引用的文件名。"""
    client = _client()
    try:
        info = await client.upload_image(image_path, subfolder="aigc_refs")
        sub = info.get("subfolder") or ""
        name = info.get("name") or Path(image_path).name
        ref = f"{sub}/{name}" if sub else name
        print(f"[上传] {image_path} -> {ref}")
        return ref
    finally:
        await client.close()


async def run_workflow(kind: str, **overrides) -> None:
    """提交 API 格式工作流并等待出图，图片同步落地本地。"""
    client = _client()
    try:
        workflow = api_workflow(kind, **overrides)

        missing = await _missing_nodes(client, workflow)
        if missing:
            print(f"[跳过] {kind}: 本机 ComfyUI 未安装节点 {missing}，工作流已创建但无法运行")
            return
        if kind == "controlnet" and overrides.get("controlnet_image", "").startswith("your_"):
            print(f"[跳过] {kind}: 需通过 --image 指定控制图（本地路径）后再跑")
            return

        queued = await client.queue_prompt(workflow)
        pid = queued.get("prompt_id", "")
        print(f"[提交] {kind:10s} prompt_id={pid}  (排队中…)")

        result = await client.wait_for_completion(pid)
        if not result.get("success"):
            print(f"[失败] {kind}: {result.get('error')}")
            return

        for node_out in result.get("outputs", {}).values():
            for img in node_out.get("images", []):
                local = sync_image_to_local(img)
                print(f"[出图] {kind:10s} {img.get('filename')} -> {local or '(未找到源文件)'}")
    finally:
        await client.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="在 ComfyUI 创建工作流并跑图")
    p.add_argument("kinds", nargs="*", help=f"要处理的模板，默认全部：{' '.join(ALL_KINDS)}")
    p.add_argument("--prompt", help="正向提示词")
    p.add_argument("--negative", help="负向提示词（FLUX 模板无此项）")
    p.add_argument("--width", type=int, help="宽")
    p.add_argument("--height", type=int, help="高")
    p.add_argument("--steps", type=int, help="采样步数")
    p.add_argument("--cfg", type=float, help="CFG")
    p.add_argument("--seed", type=int, help="随机种子")
    p.add_argument("--image", help="ControlNet / IP-Adapter 的输入图（本地路径）")
    p.add_argument("--preset", help="IP-Adapter 预设（ipadapter / ipadapter_faceid 模板生效），如 "
                                    "'PLUS (high strength)' / 'PLUS FACE (portraits)' / 'FACEID PLUS V2'")
    p.add_argument("--lora-strength", type=float, help="FaceID 配套 LoRA 强度（仅 ipadapter_faceid 模板生效）")
    p.add_argument("--provider", help="insightface 推理后端（仅 ipadapter_faceid 模板生效），如 'CPU'")
    p.add_argument("--create-only", action="store_true", help="只创建工作流，不跑图")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    kinds = args.kinds or ALL_KINDS

    # 只把显式传入的参数作为覆盖项（api_workflow 会自行忽略模板不支持的键）
    overrides = {
        k: v
        for k, v in {
            "prompt": args.prompt, "negative_prompt": args.negative,
            "width": args.width, "height": args.height,
            "steps": args.steps, "cfg": args.cfg, "seed": args.seed,
            "preset": args.preset,
            "lora_strength": args.lora_strength, "provider": args.provider,
        }.items()
        if v is not None
    }
    # 输入图先上传，让「创建工作流」里的 LoadImage 直接指向 ComfyUI 内可用的图
    if args.image:
        ref = asyncio.run(_upload_input(args.image))
        overrides["controlnet_image"] = ref
        overrides["ref_image"] = ref

    for kind in kinds:
        create_workflow(kind, **overrides)
        if not args.create_only:
            asyncio.run(run_workflow(kind, **overrides))


if __name__ == "__main__":
    main()
