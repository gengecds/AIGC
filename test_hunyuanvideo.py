#!/usr/bin/env python3
"""
HunyuanVideo 图生视频验证脚本

用法:
  1. GPU 上运行 ComfyUI（bash start_comfyui.sh）
  2. 本地运行: python3 test_hunyuanvideo.py

测试流程:
  1. 先用 SD 生成一张输入图
  2. 再提交 HunyuanVideo 图生视频工作流
  3. 拉回结果
"""

import json, os, sys, time, requests

GPU_HOST = "connect.bjb2.seetacloud.com"
GPU_PORT = 30476
GPU_USER = "root"
GPU_PASS = "900917_19871002-Gz"

COMFYUI_URL = f"http://{GPU_HOST}:8188"
WORKFLOW_PATH = os.path.join(os.path.dirname(__file__), "workflows", "img2video_hunyuan.json")


def ssh_exec(cmd):
    """通过 sshpass 在 GPU 上执行"""
    import subprocess
    full_cmd = [
        "sshpass", "-p", GPU_PASS,
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-p", str(GPU_PORT),
        f"{GPU_USER}@{GPU_HOST}",
        cmd,
    ]
    r = subprocess.run(full_cmd, capture_output=True, text=True, timeout=120)
    return r.stdout, r.stderr


def queue_workflow(workflow):
    r = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}, timeout=30)
    r.raise_for_status()
    return r.json()["prompt_id"]


def wait_for_completion(pid, timeout=300):
    for i in range(timeout):
        time.sleep(3)
        try:
            h = requests.get(f"{COMFYUI_URL}/history/{pid}", timeout=10).json()
            if pid in h:
                return h[pid]
        except:
            pass
        try:
            q = requests.get(f"{COMFYUI_URL}/queue", timeout=10).json()
            if not q.get("queue_running") and not q.get("queue_pending"):
                return requests.get(f"{COMFYUI_URL}/history/{pid}", timeout=10).json().get(pid)
        except:
            pass
    raise TimeoutError(f"等待超时 {timeout}s")


def main():
    print("=" * 55)
    print("HunyuanVideo 图生视频验证")
    print("=" * 55)

    # Step 1: 先确认 GPU 和 ComfyUI
    print("\n[1/4] 检查 GPU + ComfyUI 状态...")
    ok = False
    for i in range(3):
        try:
            r = requests.get(f"{COMFYUI_URL}/system_stats", timeout=5)
            stats = r.json()
            print(f"  ✅ ComfyUI 运行中")
            ok = True
            break
        except:
            print(f"  ⏳ ComfyUI 未响应 ({i+1}/3)...")
            if i < 2: time.sleep(5)
    if not ok:
        print("  ❌ ComfyUI 不可达。请在 GPU 上先启动：")
        print("     bash start_comfyui.sh")
        sys.exit(1)

    # Step 2: 先用 SD 生成输入图
    print("\n[2/4] SD 生成输入图...")
    sd_wf = {
        "1": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":"Realistic-Vision-V5.1.safetensors"}},
        "2": {"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}},
        "3": {"class_type":"CLIPTextEncode","inputs":{"text":"A beautiful young woman, natural lighting, detailed face","clip":["1",1]}},
        "4": {"class_type":"CLIPTextEncode","inputs":{"text":"","clip":["1",1]}},
        "5": {"class_type":"KSampler","inputs":{"seed":42,"steps":10,"cfg":7.0,"sampler_name":"euler","scheduler":"normal","denoise":1.0,"model":["1",0],"positive":["3",0],"negative":["4",0],"latent_image":["2",0]}},
        "6": {"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
        "7": {"class_type":"SaveImage","inputs":{"filename_prefix":"hyvideo_input","images":["6",0]}}
    }
    pid = queue_workflow(sd_wf)
    print(f"  ⏳ 排队中 (prompt: {pid[:8]}...)")
    result = wait_for_completion(pid)
    print(f"  ✅ SD 图生成完成")

    # 获取输出的文件名
    input_image = None
    for nid, out in result["outputs"].items():
        for img in out.get("images", []):
            input_image = img["filename"]
    if not input_image:
        print("  ❌ 未找到输出图")
        sys.exit(1)
    print(f"  📄 输入图: {input_image}")

    # Step 3: 提交 HunyuanVideo 工作流
    print("\n[3/4] 提交 HunyuanVideo 图生视频...")
    with open(WORKFLOW_PATH) as f:
        hy_wf = json.load(f)

    # 替换占位符
    for node_id, node in hy_wf.items():
        if node["class_type"] == "LoadImage":
            node["inputs"]["image"] = input_image
        if node["class_type"] == "HyVideoTextEncode":
            node["inputs"]["prompt"] = "A portrait of a young woman, slowly turning head, gentle smile, cinematic lighting, 720p, high quality"

    pid = queue_workflow(hy_wf)
    print(f"  ⏳ HunyuanVideo 排队中 (prompt: {pid[:8]}...)")
    print(f"     预计耗时: 3-5 分钟（30步/49帧/512×512）")
    result = wait_for_completion(pid, timeout=600)

    # Step 4: 导出结果
    print("\n[4/4] 导出结果...")
    output_dir = os.path.join(os.path.dirname(__file__), "storage")
    os.makedirs(output_dir, exist_ok=True)

    video_files = []
    for nid, out in result["outputs"].items():
        for img in out.get("images", []):
            fn = img["filename"]
            r = requests.get(f"{COMFYUI_URL}/view?filename={fn}&type={img.get('type','output')}", timeout=30)
            local_path = os.path.join(output_dir, fn)
            with open(local_path, "wb") as f:
                f.write(r.content)
            video_files.append(fn)
            print(f"  ✅ {fn} ({len(r.content)/1024:.0f}KB)")

    print(f"\n{'=' * 55}")
    if video_files:
        print(f"✅ HunyuanVideo 图生视频成功！")
        print(f"   共 {len(video_files)} 个文件")
        print(f"   路径: storage/")
    else:
        print(f"❌ 未生成文件")

    # 保存输出帧数
    print(f"   帧数: {len(video_files)}（49帧→30步采样→24fps≈2秒）")


if __name__ == "__main__":
    main()
