"""生图工作流模板：把代码动态构造的 ComfyUI 工作流转成可复用 JSON。

背景：项目出图走 ComfyUI 的「API 格式」工作流（代码里拼节点字典 POST /prompt），
浏览器里看不到"能拖、能 Load 的画布"。本模块提供：
  - api_workflow(kind)  生成 API 格式（后端真正执行的那份）
  - ui_workflow(kind)   把 API 格式转成 ComfyUI 前端「画布格式」
                        （nodes/links/坐标），可直接拖入 ComfyUI 网页查看/调整/另存。
两者内容同源，改一个不会和另一个漂移。
"""

from __future__ import annotations

import json
from typing import Any

from providers.comfyui.client import ComfyUIClient

# ── 模板清单（kind → 说明），前端据此渲染卡片 ─────────────────
TEMPLATES: list[dict[str, str]] = [
    {"kind": "sd15", "label": "文生图 · SD1.5", "desc": "标准 txt2img：底模 → 正/负提示 → KSampler",
     "filename": "aigc_txt2img_sd15.json", "width": 512, "height": 512},
    {"kind": "flux", "label": "文生图 · FLUX", "desc": "FLUX.1：GGUF UNET + DualCLIP + 负向零化",
     "filename": "aigc_txt2img_flux.json", "width": 1024, "height": 1024},
    {"kind": "controlnet", "label": "构图控制 · ControlNet", "desc": "用线稿/深度图约束分镜构图",
     "filename": "aigc_controlnet.json", "width": 512, "height": 512},
    {"kind": "ipadapter", "label": "角色一致 · IP-Adapter", "desc": "参考角色定妆照，出图保持脸型一致",
     "filename": "aigc_ipadapter.json", "width": 512, "height": 512},
    {"kind": "ipadapter_faceid", "label": "角色一致 · IP-Adapter FaceID", "desc": "FaceID + insightface，脸部还原度更高",
     "filename": "aigc_ipadapter_faceid.json", "width": 512, "height": 512},
]

# 模板用的示例值（打开后可在 ComfyUI 里按需修改）
_PLACEHOLDER_PROMPT = "cinematic shot, a warm street food stall at night, golden crispy pan-fried dumplings, steam rising, bokeh lights, photorealistic"
_PLACEHOLDER_NEG = "blurry, low quality, watermark, extra fingers"


# 每个模板的默认参数（也是可覆盖参数的「白名单」：不在表里的键会被忽略，
# 避免把某模板不支持的参数误传给它的 builder）。
_DEFAULTS: dict[str, dict[str, Any]] = {
    "sd15": {
        "ckpt_name": "Realistic-Vision-V5.1.safetensors",
        "prompt": _PLACEHOLDER_PROMPT, "negative_prompt": _PLACEHOLDER_NEG,
        "width": 512, "height": 512, "steps": 20, "cfg": 7.5,
        "seed": 42, "sampler": "euler", "scheduler": "normal", "batch_size": 1,
    },
    "flux": {
        "prompt": _PLACEHOLDER_PROMPT,
        "width": 1024, "height": 1024, "steps": 4, "cfg": 1.0,
        "seed": 42, "sampler": "euler", "scheduler": "simple", "batch_size": 1,
    },
    "controlnet": {
        "ckpt_name": "Realistic-Vision-V5.1.safetensors",
        "prompt": _PLACEHOLDER_PROMPT, "negative_prompt": _PLACEHOLDER_NEG,
        "controlnet_name": "control_v11p_sd15_canny.pth",
        "controlnet_image": "your_canny_sketch.png",  # 拖一张线稿进来再 Queue
        "preprocessor": "canny",  # 内置 Canny 自动提线稿，工作流自包含
        "width": 512, "height": 512, "steps": 12, "cfg": 7.5,
        "seed": 42, "controlnet_strength": 0.75, "batch_size": 1,
    },
    "ipadapter": {
        "ckpt_name": "Realistic-Vision-V5.1.safetensors",
        "prompt": _PLACEHOLDER_PROMPT, "negative_prompt": _PLACEHOLDER_NEG,
        "ref_image": "character_ref.png",  # 拖一张角色定妆照进来再 Queue
        # 下面两项是内置兜底值，实际以 config.yaml comfyui.ipadapter 为准
        "preset": "PLUS (high strength)",  # 或 "PLUS FACE (portraits)"
        "width": 512, "height": 512, "steps": 12, "cfg": 7.5,
        "seed": 42, "ipadapter_weight": 0.7, "batch_size": 1,
    },
    "ipadapter_faceid": {
        "ckpt_name": "Realistic-Vision-V5.1.safetensors",
        "prompt": _PLACEHOLDER_PROMPT, "negative_prompt": _PLACEHOLDER_NEG,
        "ref_image": "character_ref.png",  # 拖一张角色定妆照进来再 Queue
        # 以下几项是内置兜底值，实际以 config.yaml comfyui.ipadapter 为准
        "preset": "FACEID PLUS V2",  # 或 "FACEID" / "FACEID PORTRAIT (style transfer)"
        "lora_strength": 0.6, "provider": "CPU",
        "width": 512, "height": 512, "steps": 12, "cfg": 7.5,
        "seed": 42, "weight": 1.0, "weight_faceidv2": 1.0, "batch_size": 1,
    },
}

# kind → ComfyUIClient 上对应的 builder 方法名
_BUILDERS: dict[str, str] = {
    "sd15": "build_txt2img_workflow",
    "flux": "build_flux_txt2img_workflow",
    "controlnet": "build_controlnet_workflow",
    "ipadapter": "build_ipadapter_workflow",
    "ipadapter_faceid": "build_ipadapter_faceid_workflow",
}


def _configured_ipadapter(kind: str) -> dict[str, Any]:
    """IP-Adapter 模板的 preset/权重：优先取 config.yaml（当前风格可覆盖）。

    取不到时返回空 dict，回落到 _DEFAULTS 里的内置默认值。
    风格侧在 config.yaml 的 styles.<风格名>.ipadapter 段按键覆盖，
    见 config/style_resolver.ipadapter_for_style。
    """
    if kind not in ("ipadapter", "ipadapter_faceid"):
        return {}
    from config.style_resolver import ipadapter_for_style
    cfg = ipadapter_for_style()
    if kind == "ipadapter":
        return {"preset": cfg["preset"], "ipadapter_weight": cfg["weight"]}
    return {
        "preset": cfg["faceid_preset"],
        "weight": cfg["faceid_weight"],
        "weight_faceidv2": cfg["faceid_weight_faceidv2"],
        "lora_strength": cfg["faceid_lora_strength"],
        "provider": cfg["provider"],
    }


def api_workflow(kind: str, **overrides: Any) -> dict[str, Any]:
    """返回某类出图工作流的 API 格式（与生成时代码同构）。

    overrides 覆盖模板默认参数，常用键：prompt / negative_prompt / width /
    height / steps / cfg / seed，以及模板专属项（controlnet_name、
    controlnet_image、preprocessor、ref_image、preset 等）。不在该模板默认
    键里的覆盖项会被忽略。
    """
    if kind not in _DEFAULTS:
        raise KeyError(f"未知工作流模板: {kind}")
    params = {**_DEFAULTS[kind], **_configured_ipadapter(kind)}
    params.update({k: v for k, v in overrides.items() if k in params and v is not None})
    builder = getattr(ComfyUIClient, _BUILDERS[kind])
    return builder(**params)


# ════════════════════════════════════════════════════════════════
# API 格式 → 画布(UI)格式转换
# ComfyUI 前端画布 JSON 结构：nodes（含 pos/size/widgets_values）+ links。
# 我们用「有限节点白名单」精确还原本项目用到的节点槽位，其余交给前端按类型重建。
# ════════════════════════════════════════════════════════════════

# class_type → (标题, 输出[(名字,类型)], 输入名→类型)
_NODE_SCHEMA: dict[str, tuple[str, list[tuple[str, str]], dict[str, str]]] = {
    "CheckpointLoaderSimple": ("Load Checkpoint", [("MODEL", "MODEL"), ("CLIP", "CLIP"), ("VAE", "VAE")], {}),
    "UnetLoaderGGUFAdvanced": ("Load Diffusion Model (GGUF)", [("MODEL", "MODEL")], {}),
    "DualCLIPLoader": ("DualCLIP Loader", [("CLIP", "CLIP")], {}),
    "VAELoader": ("Load VAE", [("VAE", "VAE")], {}),
    "CLIPTextEncode": ("CLIP Text Encode (Prompt)", [("CONDITIONING", "CONDITIONING")], {"clip": "CLIP"}),
    "ConditioningZeroOut": ("Conditioning (Zero Out)", [("CONDITIONING", "CONDITIONING")], {"conditioning": "CONDITIONING"}),
    "EmptyLatentImage": ("Empty Latent Image", [("LATENT", "LATENT")], {}),
    "EmptySD3LatentImage": ("Empty SD3 Latent Image", [("LATENT", "LATENT")], {}),
    "KSampler": ("KSampler", [("LATENT", "LATENT")],
                 {"model": "MODEL", "positive": "CONDITIONING", "negative": "CONDITIONING", "latent_image": "LATENT"}),
    "VAEDecode": ("VAE Decode", [("IMAGE", "IMAGE")], {"samples": "LATENT", "vae": "VAE"}),
    "SaveImage": ("Save Image", [], {"images": "IMAGE"}),
    "LoadImage": ("Load Image", [("IMAGE", "IMAGE")], {}),
    "Canny": ("Canny", [("IMAGE", "IMAGE")], {"image": "IMAGE"}),
    "ControlNetLoader": ("Load ControlNet Model", [("CONTROL_NET", "CONTROL_NET")], {}),
    "ControlNetApply": ("Apply ControlNet", [("CONDITIONING", "CONDITIONING")],
                        {"conditioning": "CONDITIONING", "control_net": "CONTROL_NET", "image": "IMAGE"}),
    "IPAdapterModelLoader": ("IPAdapter Model Loader", [("IPADAPTER", "IPADAPTER")], {}),
    "CLIPVisionLoader": ("Load CLIP Vision", [("CLIP_VISION", "CLIP_VISION")], {}),
    "CLIPVisionEncode": ("CLIP Vision Encode", [("CLIP_VISION_OUTPUT", "CLIP_VISION_OUTPUT")],
                         {"clip_vision": "CLIP_VISION", "image": "IMAGE"}),
    "IPAdapterUnifiedLoader": ("IPAdapter Unified Loader", [("MODEL", "MODEL"), ("IPADAPTER", "IPADAPTER")], {"model": "MODEL"}),
    "IPAdapter": ("IPAdapter", [("MODEL", "MODEL")],
                  {"model": "MODEL", "ipadapter": "IPADAPTER", "image": "IMAGE"}),
    "IPAdapterUnifiedLoaderFaceID": ("IPAdapter Unified Loader FaceID",
                                     [("MODEL", "MODEL"), ("IPADAPTER", "IPADAPTER")], {"model": "MODEL"}),
    "IPAdapterFaceID": ("IPAdapter FaceID", [("MODEL", "MODEL"), ("IMAGE", "IMAGE")],
                        {"model": "MODEL", "ipadapter": "IPADAPTER", "image": "IMAGE"}),
}

_LAYER_OFFSET = 300  # 每一层的横向间距（画布坐标用，属布局几何量）
_ROW_PITCH = 190     # 同一层多个节点时的纵向间距


def _layers(api: dict[str, Any]) -> dict[str, int]:
    """按「依赖深度」给节点分层：sources 层 0，越靠输出层越大（简单自动排版用）。"""
    depth: dict[str, int] = {}

    def walk(nid: str) -> int:
        if nid in depth:
            return depth[nid]
        node = api[nid]
        deps = [v[0] for v in node.get("inputs", {}).values() if isinstance(v, list) and v]
        d = 0 if not deps else max(walk(p) for p in deps) + 1
        depth[nid] = d
        return d

    for nid in api:
        walk(nid)
    return depth


def api_to_ui(api: dict[str, Any]) -> dict[str, Any]:
    """API 格式 → ComfyUI 画布(UI)格式，带简单自动排版，可直接拖进网页加载。"""
    depth = _layers(api)
    # 层 → 该层节点序号（用于纵向摆放）
    col_count: dict[int, int] = {}

    nodes: list[dict[str, Any]] = []
    links: list[list[Any]] = []
    link_id = 1

    # 第一遍：建节点外壳 + 输入槽的 link 占位，记录 输出(slot)→link 候选
    for key, node in api.items():
        nid = int(key)
        ct = node["class_type"]
        title, outputs, in_types = _NODE_SCHEMA.get(
            ct, (ct, [("OUTPUT", "OUTPUT")], {}))

        # 输入槽：只有「值为 ["源节点", 源输出]」的才算连线；标量(字符串/数字)归 widgets
        inputs: list[dict[str, Any]] = []
        for in_name, val in node.get("inputs", {}).items():
            if isinstance(val, list) and val:
                inputs.append({"name": in_name, "type": in_types.get(in_name, "STRING"), "link": None})
        widgets = [v for v in node.get("inputs", {}).values() if not isinstance(v, list)]

        # 简单分层排版：把每个节点放横坐标=层、纵坐标=该层第几个
        col_count[depth[key]] = col_count.get(depth[key], 0)
        x = 60 + depth[key] * _LAYER_OFFSET
        y = 60 + col_count[depth[key]] * _ROW_PITCH
        col_count[depth[key]] += 1
        # 按 widget 数量估算高度，防止文字溢出（几何近似，非 UI token）
        h = 90 + 34 * max(len(widgets) - 1, 0)

        nodes.append({
            "id": nid, "type": ct, "pos": [x, y],
            "size": [340, min(h, 320)],
            "flags": {}, "order": nid, "mode": 0,
            "inputs": inputs, "outputs": [
                {"name": n, "type": t, "slot_index": i, "links": []}
                for i, (n, t) in enumerate(outputs)
            ],
            "properties": {"Node name for S&R": ct},
            "widgets_values": widgets,
            "title": title,
        })

    # 第二遍：补 link 连接（输入槽 ← 源节点输出），并登记到双方槽位
    nid_lookup = {n["id"]: n for n in nodes}
    for key, node in api.items():
        nid = int(key)
        for in_name, val in node.get("inputs", {}).items():
            if not (isinstance(val, list) and val):
                continue
            src_id, src_slot = int(val[0]), val[1]
            dst = nid_lookup[nid]
            slot_obj = next((s for s in dst["inputs"] if s["name"] == in_name), None)
            src = nid_lookup[src_id]
            src_out = src["outputs"][src_slot] if src_slot < len(src["outputs"]) else None
            if slot_obj is None or src_out is None:
                continue
            link_type = src_out["type"]
            slot_obj["link"] = link_id
            src_out["links"].append(link_id)
            links.append([link_id, src_id, src_slot, nid, dst["inputs"].index(slot_obj), link_type])
            link_id += 1

    return {
        "last_node_id": max((n["id"] for n in nodes), default=0),
        "last_link_id": max((l[0] for l in links), default=0),
        "nodes": nodes,
        "links": links,
        "groups": [], "config": {}, "extra": {}, "version": 0.4,
    }


def ui_workflow(kind: str, **overrides: Any) -> dict[str, Any]:
    """返回某类出图工作流的画布(UI)格式（overrides 同 api_workflow）。"""
    return api_to_ui(api_workflow(kind, **overrides))


if __name__ == "__main__":
    import sys
    kind = sys.argv[1] if len(sys.argv) > 1 else "sd15"
    fmt = sys.argv[2] if len(sys.argv) > 2 else "ui"
    wf = ui_workflow(kind) if fmt == "ui" else api_workflow(kind)
    print(json.dumps(wf, ensure_ascii=False, indent=2))
