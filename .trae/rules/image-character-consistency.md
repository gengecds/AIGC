---
description: FLUX 出图的角色一致性规则——参考图/IP-Adapter 无效，一致性 100% 靠 prompt
alwaysApply: true
---

# FLUX 角色一致性规则

## 核心事实

当主风格底模是 FLUX（`image_model_type == "flux"`，即「写实风格」「电影级质感」这类风格）时：

- **IP-Adapter / ControlNet 完全不生效**，挂上去也不影响出图；
- 角色一致性 **100% 由 `sd_prompt` 决定**，没有任何「参考图锁脸」的兜底。

## 硬性要求

1. **每一个出现角色的镜头，`sd_prompt` 开头必须逐字重复统一的角色描述**，至少含
   「发色 + 发型 + 标志性服装」。标准前缀示例：

   ```
   Lin Zhao, 25-year-old Chinese woman, low bun with dark red silk flower and silver tassel,
   red Chinese dress with mandarin collar and gold embroidered lotus patterns, <本镜的画面描述…>
   ```

2. **不能只在首镜写，也不能指望模型「知道上文」**。实测漏写发型的那一镜直接出成另一个人
   （红裙盘发女歌手 → 短发路人），且质检仍给 100 分——**质检分数不能用来判断角色是否漂移**。

3. 加权要用**具体词 + 温和权重**：`(sakura pink hair:1.3)`。
   实测 `(pink hair:1.6)` 会让画面崩坏、人物消失（QC 78），再加 `black hair` 负向掉到 64。

4. 相邻镜头容易被底模默认审美带偏时，在 `sd_negative` 里追加对冲词，如
   `short hair, bob cut, pixie cut`。

5. 超特写（面部大特写）里发型本来就不入画，视觉模型会「猜」出发型不一致，
   这类镜头的发型判读不可信，不必为此重出。

## 配套约束

- **出图速度 ≈ 15.3 分钟/张**（`flux1-dev-Q5_K_S.gguf`），重跑成本极高：
  改 prompt 前先确认真的必须改，能复用已有图就不要重出。
- **底模由 styles[0] 决定**（`image_ckpt_for_style()` 取第一个风格），
  风格列表的**顺序**会改变整个出图引擎，别随意调序。
- SD1.5 的 CLIP **读不懂中文**：`sd_prompt` 里的中文段落纯属挤占 77 token 窗口，要丢弃；
  风格关键词也必须有英文等价词（见 `config/config.yaml` 的 styles）。
- LoRA 必须与底模匹配（SD1.5 与 FLUX 的 LoRA 不能混挂），已有引擎过滤逻辑见
  `config/style_resolver.py` 的 `_lora_fits_engine()`。
