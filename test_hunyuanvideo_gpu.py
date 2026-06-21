#!/usr/bin/env python3
"""HunyuanVideo 图生视频验证 - 在 GPU 本地直接运行"""
import requests, json, time, sys, os

COMFY = "http://localhost:8188"

def q(wf):
    r = requests.post(f"{COMFY}/prompt", json={"prompt": wf})
    if not r.ok:
        print(f"  错误: {r.text[:300]}")
    r.raise_for_status()
    return r.json()["prompt_id"]

def wait(pid, timeout=600):
    for i in range(timeout):
        time.sleep(3)
        try:
            h = requests.get(f"{COMFY}/history/{pid}").json()
            if pid in h:
                return h[pid]
        except: pass
        try:
            qq = requests.get(f"{COMFY}/queue").json()
            if not qq.get("queue_running") and not qq.get("queue_pending"):
                h = requests.get(f"{COMFY}/history/{pid}").json()
                if pid in h: return h[pid]
                return None
        except: pass
    raise TimeoutError("超时")

print("="*60)
print("HunyuanVideo 图生视频测试")
print("="*60)

# Step 1: SD 生成输入图
print("\n[1/4] SD 生成输入图...")
sd_wf = {
    "1": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"Realistic-Vision-V5.1.safetensors"}},
    "2": {"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}},
    "3": {"class_type":"CLIPTextEncode","inputs":{"text":"beautiful young woman, natural light, detailed face, portrait","clip":["1",1]}},
    "4": {"class_type":"CLIPTextEncode","inputs":{"text":"","clip":["1",1]}},
    "5": {"class_type":"KSampler","inputs":{"seed":42,"steps":10,"cfg":7,"sampler_name":"euler","scheduler":"normal","denoise":1,"model":["1",0],"positive":["3",0],"negative":["4",0],"latent_image":["2",0]}},
    "6": {"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
    "7": {"class_type":"SaveImage","inputs":{"filename_prefix":"hyvideo_input","images":["6",0]}}
}
pid = q(sd_wf)
print(f"  prompt: {pid[:8]}...")
r = wait(pid)
img_name = None
for nid,out in r["outputs"].items():
    for img in out.get("images",[]):
        img_name = img["filename"]
print(f"  ✅ 输入图: {img_name}")

# 复制到 ComfyUI input/ 目录
import shutil
src = f"/root/ComfyUI/output/{img_name}"
dst = f"/root/ComfyUI/input/{img_name}"
if not os.path.exists(dst):
    shutil.copy2(src, dst)
print(f"  ✅ 复制到 input/: {img_name}")

# Step 2: 提交图生视频（去掉不存在的ImageResize节点）
print("\n[2/4] 提交 HunyuanVideo 图生视频...")
print("     节点: LoadImage→TextEncoder+ModelLoader+VAELoader")
print("     →TextEncode→VideoEncode→Sampler(30步/49帧)→Decode→Save")

wf = {
    "8": {"class_type": "LoadImage", "inputs": {"image": img_name}},
    "9": {"class_type": "HyVideoVAELoader", "inputs": {"model_name": "hunyuan_video_vae_bf16.safetensors", "precision": "bf16"}},
    "10": {"class_type": "DownloadAndLoadHyVideoTextEncoder", "inputs": {"llm_model": "Kijai/llava-llama-3-8b-text-encoder-tokenizer", "clip_model": "disabled", "precision": "bf16", "apply_final_norm": False, "hidden_state_skip_layer": 2, "quantization": "disabled"}},
    "11": {"class_type": "HyVideoModelLoader", "inputs": {"model": "HunyuanVideo/hunyuan_video_720_cfgdistill_fp8_e4m3fn.safetensors", "base_precision": "bf16", "quantization": "fp8_e4m3fn_fast", "load_device": "main_device"}},
    "12": {"class_type": "HyVideoTextEncode", "inputs": {"text_encoders": ["10",0], "prompt": "A beautiful young woman portrait, slowly turning her head, gentle smile, cinematic lighting, high quality, detailed", "force_offload": True, "prompt_template": "video"}},
    "13": {"class_type": "HyVideoEncode", "inputs": {"vae": ["9",0], "image": ["8",0], "enable_vae_tiling": True, "temporal_tiling_sample_size": 64, "spatial_tile_sample_min_size": 256, "auto_tile_size": True}},
    "14": {"class_type": "HyVideoSampler", "inputs": {"model": ["11",0], "hyvid_embeds": ["12",0], "width": 512, "height": 512, "num_frames": 25, "steps": 15, "embedded_guidance_scale": 6.0, "flow_shift": 9.0, "seed": 42, "force_offload": True, "samples": ["13",0], "denoise_strength": 0.8}},
    "15": {"class_type": "HyVideoDecode", "inputs": {"vae": ["9",0], "samples": ["14",0], "enable_vae_tiling": True, "temporal_tiling_sample_size": 64, "spatial_tile_sample_min_size": 256, "auto_tile_size": True}},
    "16": {"class_type": "SaveImage", "inputs": {"filename_prefix": "hyvideo_output", "images": ["15",0]}}
}

pid = q(wf)
print(f"  prompt: {pid[:8]}...")
print("  预计: 1-2分钟 (25帧/15步)")
sys.stdout.flush()

start = time.time()
r = wait(pid, timeout=600)
elapsed = time.time() - start

# Step 3: 导出
print(f"\n[3/4] 结果 ({elapsed:.0f}秒)")
outdir = "/root/ComfyUI/output"
files = []
for nid,out in r["outputs"].items():
    for img in out.get("images",[]):
        fn = img["filename"]
        fp = os.path.join(outdir, fn)
        sz = os.path.getsize(fp) if os.path.exists(fp) else 0
        files.append((fn, sz))
        print(f"  ✅ {fn} ({sz/1024:.0f}KB)")

print(f"\n{'='*60}")
if files:
    print(f"✅ HunyuanVideo 图生视频成功！{elapsed:.0f}s")
    print(f"   文件: {len(files)}")
    print(f"   帧率推算: {len(files)}帧 / {elapsed:.0f}s ≈ 2秒@24fps")
else:
    print("❌ 无输出")
