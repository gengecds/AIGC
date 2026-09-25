# 14 个风格 LoRA 手动下载清单

本文档是「下载缺少模型」任务的一部分：为 14 个风格补对应的风格 LoRA（全部基于 **SD 1.5** 基座）。由于这些 LoRA 以 Civitai（C 站）为主，需要登录后用**代理/科学上网**访问才能稳定下载；HuggingFace 上可直接用 `aria2c` 拉直链。

> 版本匹配铁律：**SD 1.5 的 LoRA 只能配 SD 1.5 底模**。当前 14 个风格在 [config.yaml](file:///Users/a715/git/AIGC/config/config.yaml) 中均为 `image_model_type: sd15`，因此只下载 `Base Model = SD 1.5` 的 LoRA，切勿误下 SDXL 版本。

## 通用说明

- **存放目录**：`/Users/a715/git/ComfyUI/ComfyUI-Installs/ComfyUI/ComfyUI/models/loras/`
  - 建议建子目录 `SD1.5/` 区分基座：`.../loras/SD1.5/`（ComfyUI 会自动递归扫描）。
- **文件格式**：只认 `.safetensors`。
- **Civiai 下载格式**（若复制页面上的直链）：`https://civitai.com/api/download/models/<modelVersionId>`
- **HuggingFace 直链格式**（配合 aria2 使用）：
  ```
  aria2c -x 8 -s 8 --all-proxy=http://127.0.0.1:7890 -c --file-allocation=none \
    -d <目标目录> -o <文件名> "https://huggingface.co/<repo>/resolve/main/<file>"
  ```
- **触发词**：每个 LoRA 页面底部（C 站）/模型卡（HF）会标注 Trigger Words，使用时要写进正向提示词；无触发词的风格 LoRA 仅靠 `keyword`（如 `cyberpunk`、`steampunk`）即可激活。
- **建议权重**：风格 LoRA 一般 `0.6~0.9`，太高压制内容、太低看不出风格，可按图微调。
- **验证**：下载后重启 ComfyUI，在 `loras` 相关节点下拉列表里能看到即可。

---

## 14 个风格 LoRA 清单

| # | 风格（config 名） | LoRA 名 | 基座 | 首选来源 | 触发词 / 备注 | 建议权重 |
|---|---|---|---|---|---|---|
| 1 | 国风古风 | Chinese Ink Painting (水墨) | SD1.5 | Civitai **[墨心 MoXin](https://civitai.com/models/12597)**（水墨/国画） | 触发词 `shukezouma`；水墨留白 | 0.7 |
| 2 | 国风古风 | Guofeng LoRA (国风) | SD1.5 | Civitai [GuoFeng3 Lora](https://civitai.com/models/11352/guofeng3lora)；HF `andzhang01/SD-GF`、`xiaolxl/GuoFeng3` | 无固定触发词，写「Chinese traditional, guofeng」 | 0.7 |
| 3 | 复古港风 | Retro Hong Kong | SD1.5 | Civitai 搜索 **"hong kong retro" / "港风 90s"** | 写「80s HK movie, film grain, neon」 | 0.7 |
| 4 | 蒸汽波 | Vaporwave | SD1.5 | Civitai 搜索 **"vaporwave"** | 写「vaporwave, pastel, VHS」 | 0.7 |
| 5 | 蒸汽朋克 | Steampunk | SD1.5 | Civitai 搜索 **"steampunk"** | 写「steampunk, brass gears, victorian」 | 0.7 |
| 6 | 赛博朋克 | Cyberpunk | SD1.5 | Civitai 搜索 **"cyberpunk" / "neon glow"**（Lykon「Lucy」等） | 写「cyberpunk, neon, rain, city night」 | 0.6 |
| 7 | 治愈系ins | Cozy Daily | SD1.5 | Civitai 搜索 **"cozy" / "pastel" / "lofi"** | 暖调、奶油色、慢生活 | 0.6 |
| 8 | 极简主义 | Minimalist Morandi | SD1.5 | Civitai 搜索 **"morandi" / "minimalist"** | 高级灰、留白构图 | 0.6 |
| 9 | 3D卡通 | 3D Render | SD1.5 | HF [3d-redmond-1-5v-3d-render-style-for-liberte-redmond-sd-1-5](https://huggingface.co/artificialguybr/3d-redmond-1-5v-3d-render-style-for-liberte-redmond-sd-1-5)；Civitai 搜索 "3d render" | 皮克斯/黏土软渲 | 0.7 |
| 10 | Q版卡通 | Chibi | SD1.5 | Civitai 搜索 **"chibi" / "q版"** | 写「chibi, big eyes, sticker」 | 0.7 |
| 11 | 像素艺术 | Pixel Art | SD1.5 | HF [pixelartredmond-1-5v-pixel-art-loras-for-sd-1-5](https://huggingface.co/artificialguybr/pixelartredmond-1-5v-pixel-art-loras-for-sd-1-5)；Civitai 搜索 "pixel art" | 触发词 `Pixel Art`, `PixArFK` | 0.8 |
| 12 | 古典油画 | Classical Oil Painting | SD1.5 | Civitai 搜索 **"oil painting" / "classical oil"** | 厚重笔触、明暗对照、暖金调 | 0.7 |
| 13 | 水彩 | Watercolor | SD1.5 | Civitai [Watercolor Painting](https://civitai.com/models/749060)；Civitai 搜索 "watercolor" | 晕染、纸纹 | 0.7 |
| 14 | 素描 | Sketch Pencil | SD1.5 | Civitai 搜索 **"sketch" / "pencil"** | 铅笔排线、灰阶 | 0.7 |

---

## 推荐优先级（如果只想先挑几个最常用）

先从 **Civitai 搜索**最对味、效果最稳的 4 个开始，其余按需补充：

1. **Chinese Ink Painting（国风）** — C 站「墨心 MoXin」，最成熟的水墨 LoRA。
2. **Pixel Art（像素）** — HF redmond 系列，直链好下、触发词明确（`PixArFK`）。
3. **3D Render（3D 卡通）** — HF redmond 系列，直链好下。
4. **Guofeng（国风）** — C 站 GuoFeng3 LoRA，与「国风古风」风格绑定。

## 与底模的搭配建议

- 水墨/国风：`v1-5-pruned-emaonly` 或 `Anything V5` 底模 + 对应 LoRA 最出味（config 中「国风古风」默认 `image_ckpt: v1-5-pruned-emaonly`）。
- Q版/日系：`Anything V5` + Chibi LoRA（config 中「Q版卡通」默认 `Anything V5`）。
- 其余写实/风格类：多走 `v1-5-pruned-emaonly`（config 中大部分风格默认该底模）。

> 注：`v1-5-pruned-emaonly.safetensors` 已在基础下载清单内，`Realistic-Vision-V5.1`、`Anything V5` 正在后台下载中，LoRA 完成后即可直接组合使用。
