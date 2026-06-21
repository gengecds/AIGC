# AI 漫剧创作平台 — 部署手册

> **适用场景**：你购买了带 GPU 的本地电脑（或租到新 GPU 服务器），把整个系统迁过去直接跑。
> 
> **迁移前状态**：代码在 Mac 上，ComfyUI 在 AutoDL 远程服务器，SSH 隧道连接。
> **迁移后状态**：全都在一台本地 GPU 电脑上，零延迟，无需隧道。

---

## 一、前置要求

### 硬件

| 组件 | 最低要求 | 推荐 |
|:---|:---|:---|
| GPU | RTX 3060 12GB | RTX 4090 24GB |
| 内存 | 16GB | 32GB |
| 硬盘 | 50GB 空闲 | 200GB+（模型+产出） |
| 系统 | Ubuntu 22.04+ / Windows WSL2 | Ubuntu 24.04 |

### 软件

- Python 3.10～3.14（推荐 3.10，ComfyUI 兼容最好）
- Git
- Docker（可选，推荐）
- NVIDIA 驱动 + CUDA 12.1+
- ffmpeg
- （可选）sshpass — 本地 GPU 不需要，远程 AutoDL 才需要

---

## 二、一键部署到新 GPU 电脑

> 新电脑到手后，按顺序执行以下步骤。

### 2.1 安装基础依赖

```bash
# Ubuntu
sudo apt update && sudo apt install -y git python3 python3-pip python3-venv ffmpeg

# 确认 NVIDIA 驱动
nvidia-smi  # 应该能看到 GPU 信息
```

### 2.2 克隆项目代码

```bash
git clone https://github.com/你的/aigc-platform.git
cd aigc-platform
```

### 2.3 安装 Python 依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2.4 安装 ComfyUI + 模型

```bash
# 克隆 ComfyUI
git clone https://github.com/comfyanonymous/ComfyUI.git
cd ComfyUI
pip install -r requirements.txt
cd ..

# 一键部署 GPU 环境（自动装自定义节点 + SD 模型）
bash scripts/setup_gpu.sh

# 手动确认模型已安装
ls ComfyUI/models/checkpoints/
# 应该看到: Realistic-Vision-V5.1.safetensors
```

### 2.5 配置引擎

编辑 `config/config.yaml`：

```yaml
engine:
  image_provider: "comfyui"    # ← 从 mock 改过来
  video_provider: "comfyui"    # ← 从 mock 改过来
  llm_provider: "deepseek"     # DeepSeek API（保持不变）

comfyui:
  server_addr: "127.0.0.1"     # ← 改为本地地址
  server_port: 8188            # ← ComfyUI 本地端口
  # gpu_host, gpu_port, gpu_pass  — 本地不需要，保留即可
```

### 2.6 配置 DeepSeek API

```bash
# 方式 A：环境变量（推荐）
export DEEPSEEK_API_KEY="sk-your-real-key"

# 方式 B：写 .env 文件
echo 'DEEPSEEK_API_KEY=sk-your-real-key' > .env

# 方式 C：直接改 config.yaml
```

### 2.7 启动

**终端 1 — ComfyUI：**

```bash
cd ComfyUI
source ../.venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188 --highvram
```

**终端 2 — 后端服务：**

```bash
cd aigc-platform
source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8888 --reload
```

### 2.8 验证

```bash
# 1. ComfyUI 是否正常
curl -s http://127.0.0.1:8188/api/system/stats

# 2. 后端是否正常
curl -s http://127.0.0.1:8888/health

# 3. 提交一次 full pipeline
curl -X POST http://127.0.0.1:8888/api/v1/pipeline/run \
  -H "Content-Type: application/json" \
  -d '{"story": "程序员穿越到猫娘世界", "max_shots": 2}'
```

如果全部返回 200，部署成功 ✅

---

## 三、旧机→新机完整迁移方案

> **适用场景**：当前项目在 Mac（代码 + 数据库 + 产出）+ AutoDL（ComfyUI GPU）
> 买了新的本地 GPU 电脑后，把全套环境搬过去。

### 3.1 备份：从 Mac 打包所有数据

在当前的 Mac 上执行：

```bash
cd /Users/mac/git/AIGC

# 创建完整数据压缩包
tar czf ~/Desktop/aigc_backup_$(date +%Y%m%d).tar.gz \
  .git/                         \  # 代码（含 git 历史）
  config/config.yaml            \  # 配置
  .env                          \  # API 密钥
  storage/ai_drama.db           \  # 数据库
  storage/checkpoints/          \  # Pipeline 断点
  storage/output/               \  # 产出视频/字幕/图片
  workflows/                    \  # ComfyUI 工作流模板
  scripts/                      \  # 部署脚本
  Dockerfile docker-compose.yml \  # Docker 文件
  requirements.txt              \  # Python 依赖
  docs/                            # 文档

ls -lh ~/Desktop/aigc_backup_*.tar.gz
```

> **传输方式**：
> - 同局域网 → `scp ~/Desktop/aigc_backup_*.tar.gz user@新机IP:~/`
> - 不同网络 → USB 硬盘拷贝，或者上传云盘后下载

### 3.2 在新电脑上恢复

```bash
# 1. 安装基础依赖
sudo apt update && sudo apt install -y git python3 python3-pip python3-venv ffmpeg
nvidia-smi  # 确认 GPU 可用

# 2. 解压备份
tar xzf ~/aigc_backup_20260621.tar.gz -C ~/aigc-platform
cd ~/aigc-platform

# 3. 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 4. 安装 ComfyUI + 模型 + 自定义节点
bash scripts/setup_gpu.sh

# 5. 修改配置（只改这 3 行）
# vim config/config.yaml
#   image_provider: "comfyui"    # ← 从 mock 改
#   video_provider: "comfyui"    # ← 从 mock 改
#   server_addr: "127.0.0.1"     # ← 从远程改本地

# 6. 配置 DeepSeek
export DEEPSEEK_API_KEY="sk-your-real-key"

# 7. 启动
# 终端 1:
cd ~/aigc-platform/ComfyUI && source ../.venv/bin/activate
python main.py --listen 127.0.0.1 --port 8188 --highvram

# 终端 2:
cd ~/aigc-platform && source .venv/bin/activate
uvicorn api.main:app --host 0.0.0.0 --port 8888 --reload
```

### 3.3 迁移前后对比

| 对比项 | 旧模式（Mac + AutoDL） | **新模式（本地 GPU 电脑）** |
|:---|:---|:---|
| ComfyUI 位置 | 远程 AutoDL 服务器 | ✅ 本地 |
| 图片传输 | SSH 隧道 + scp | ✅ 直接本地文件系统 |
| 延迟 | 网络延迟 50-200ms | ✅ 零延迟 |
| 视频下载 | scp 下载到本地 | ✅ 直接写入磁盘 |
| SSH 隧道 | 必须启动 | ❌ 不需要 |
| sshpass | 必须安装 | ❌ 不需要 |
| 网络依赖 | 强依赖网络 | ❌ 完全离线可用 |
| `_cp_to_input` | 走 SSH 远程复制 | ✅ 本地 cp 即时完成 |
| 启动复杂度 | 3 步（隧道+ComfyUI+后端） | ✅ 2 步（ComfyUI+后端） |

### 3.4 需要改的配置（仅此 3 处）

```yaml
# config/config.yaml 中
engine:
  image_provider: "comfyui"    # 原来可能是 "mock"
  video_provider: "comfyui"    # 原来可能是 "mock"
  llm_provider: "deepseek"     # 原来可能是 "mock"

comfyui:
  server_addr: "127.0.0.1"     # 原来可能是 AutoDL IP
  server_port: 8188            # 原来可能是 18188（隧道端口）
```

其他所有文件（代码、数据库、checkpoint、产出、workflows）原样使用，**零改动**。

### 3.5 迁移验证清单

- [ ] 数据库已恢复：`python3 -c "import sqlite3; c=sqlite3.connect('storage/ai_drama.db'); print(c.execute('SELECT COUNT(*) FROM stories').fetchone())"` ✅
- [ ] ComfyUI 正常：`curl http://127.0.0.1:8188/api/system/stats` → 200 ✅
- [ ] 后端正常：`curl http://127.0.0.1:8888/health` → 200 ✅
- [ ] GPU 可用：`nvidia-smi` → 正常显示 ✅
- [ ] 一次 pipeline 跑通 ✅

---

## 四、Docker 部署（可选）

> 如果想把后端容器化运行（推荐生产环境）。

### 4.1 构建并运行

```bash
cd aigc-platform
docker compose up -d --build
```

### 4.2 验证

```bash
curl http://localhost:8888/health
# 返回 {"status":"ok"}
```

### 4.3 查看日志

```bash
docker compose logs -f
```

### 4.4 停止

```bash
docker compose down
```

> ⚠️ Docker 部署不包含 ComfyUI。ComfyUI 仍需单独启动（终端 1 的方式）。
> 如需 Docker 化 ComfyUI（需要 nvidia-container-toolkit），参考附录 C。

---

## 五、故障排查

### ComfyUI 启动报错

| 症状 | 原因 | 解决 |
|:---|:---|:---|
| `address already in use` | 端口被占 | `fuser -k 8188/tcp` 再重试 |
| `CUDA out of memory` | VRAM 不够 | 加 `--normalvram` 或 `--lowvram` |
| `ModuleNotFoundError` | 缺节点 | `cd custom_nodes && git clone <仓库>` |
| `FileNotFoundError` | 模型没装 | 重跑 `setup_gpu.sh` |

### 后端启动报错

| 症状 | 原因 | 解决 |
|:---|:---|:---|
| `Connection refused` | ComfyUI 没启动 | 先启动 ComfyUI |
| `Invalid API key` | DeepSeek 密钥不对 | 检查 `DEEPSEEK_API_KEY` |
| `sqlite3.OperationalError` | 数据库损坏 | 删除 `storage/ai_drama.db` 重新跑 |
| ModuleNotFoundError | 环境没激活 | `source .venv/bin/activate` |

### Pipeline 跑不通

| 症状 | 原因 | 解决 |
|:---|:---|:---|
| 剧本为空 | DeepSeek API 限流 | 等 30 秒重试 |
| 出图 400 | Workflow 模型名不对 | 确认 `checkpoints/` 目录文件名 |
| 视频失败 | HunyuanVideo 节点没装 | 检查 `ComfyUI-HunyuanVideoWrapper` |
| Scheduler 卡住 | `_noget` 阻塞 | Ctrl+C 重跑，加 `--reload` |

---

## 附录

### A. 项目文件结构

```
aigc-platform/
├── agents/              # 8 个 Agent 实现
├── providers/           # 引擎适配（ComfyUI/DeepSeek/Mock）
├── pipeline/            # 管线调度 + checkpoint
├── api/                 # FastAPI 后端
├── frontend/            # 前端页面
├── workflows/           # ComfyUI JSON 工作流模板
├── config/              # 配置中心
│   └── config.yaml      # ← 部署时改这里
├── storage/             # 数据持久化
│   ├── ai_drama.db      # 数据库
│   ├── checkpoints/     # Pipeline 断点
│   └── output/          # 产出视频/字幕
├── scripts/
│   └── setup_gpu.sh     # GPU 环境一键部署
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

### B. 引擎配置速查表

| 引擎 | `image_provider` | `video_provider` | `llm_provider` | 适用场景 |
|:---|:---|:---|:---|:---|
| 全 mock | `mock` | `mock` | `mock` | 开发调试、无 GPU |
| 只有 LLM | `mock` | `mock` | `deepseek` | 只测剧本+分镜 |
| 全真实 | `comfyui` | `comfyui` | `deepseek` | **生产运行** |
| 本地方案 | `comfyui` | `comfyui` | `ollama` | 完全离线（需本地模型） |

### C. ComfyUI Docker 化（进阶）

如果想把 ComfyUI 也跑在 Docker 里（需要 nvidia-container-toolkit）：

```yaml
# docker-compose.gpu.yml
services:
  comfyui:
    image: comfyui:latest
    build: ./ComfyUI
    ports:
      - "8188:8188"
    volumes:
      - ./ComfyUI/models:/root/ComfyUI/models
      - ./ComfyUI/custom_nodes:/root/ComfyUI/custom_nodes
      - ./ComfyUI/output:/root/ComfyUI/output
      - ./ComfyUI/input:/root/ComfyUI/input
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command: python main.py --listen 0.0.0.0 --port 8188 --highvram
```

**目前不需要做这个。** 等买了 GPU 电脑，ComfyUI 直接在终端跑即可，Docker 化只是锦上添花。

---

### D. 验证清单（部署完成后逐项打勾）

- [ ] `nvidia-smi` 显示 GPU ✅
- [ ] `python --version` ≥ 3.10 ✅
- [ ] `pip install -r requirements.txt` 成功 ✅
- [ ] ComfyUI 在 8188 端口启动 ✅
- [ ] 后端在 8888 端口启动 ✅
- [ ] `curl http://localhost:8888/health` → 200 ✅
- [ ] `config.yaml` 引擎已改为 `comfyui` ✅
- [ ] DeepSeek API key 已配置 ✅
- [ ] 数据库迁移恢复：`ls storage/ai_drama.db` ✅
- [ ] Docker 可选：`docker compose up -d` 成功 ✅
- [ ] Pipeline 全链路跑通 ✅
