# AI 漫剧全流程制作 Skill

[![Claude Code Skill](https://img.shields.io/badge/Claude%20Code-Skill-blue)](https://claude.ai/code)

一个面向 [Claude Code](https://claude.ai/code) 的 AI 漫剧/短剧全流程制作 Skill。从一句话故事创意到完整视频成片，覆盖剧本创作、美术风格设计、分镜拆解、AI 生图/生视频、配音、音效、字幕、视频合成全链路。

## 功能概览

```
一句话创意 → 剧本 → 美术风格 → 分镜表 → AI生图 → AI生视频 → 配音/音效/字幕 → 最终成片
```

| 阶段 | 功能 | 说明 |
|------|------|------|
| Phase 1 | 剧本创作 | 三幕式结构，开场即钩子 |
| Phase 2 | 美术风格 | 色彩方案、光影方案、角色三视图提示词 |
| Phase 3 | 分镜拆解 | 1-3s 镜头拆分，景别/台词/音效标注 |
| Phase 4 | 文生图提示词 | 首帧/尾帧/代表画面三套提示词 |
| Phase 5 | 图生视频提示词 | 动作描述 + 镜头运动 + 情绪氛围 |
| Phase 6 | AI 生图/生视频 | 火山引擎即梦AI + 海螺AI，批量生成 |
| Phase 7 | 全流程输出 | Markdown 表格 + Excel 交付 |
| Phase 8 | 后期制作 | TTS 配音 + 音效 + BGM + 字幕 + 合成成片 |

### 核心特性

- **模块化工作流**：每个阶段独立可用，也可 `漫剧:全流程` 一键串联
- **知识库驱动**：内置 5000+ 提示词词汇库、300+ 电影风格库
- **双 TTS 引擎**：edge-tts（免费）+ 豆包大模型语音（高质量）
- **Doctor 检查**：合成前自动验证素材完整性和时长匹配
- **工作区隔离**：每个项目独立目录，互不干扰
- **平台通用**：提示词适配即梦/可灵/LibLib/Vidu 等所有平台

## 安装

### 前置条件

- [Claude Code](https://claude.ai/code) CLI 已安装并登录
- Python 3.10+
- ffmpeg（视频合成需要）

### 方式一：通过 Plugin 命令安装（推荐）

在 Claude Code 中执行：

```
/plugin marketplace add GitDzreal93/x-video-skill
/plugin install x-video-skill
```

### 方式二：手动克隆

```bash
git clone https://github.com/GitDzreal93/x-video-skill.git ~/.claude/skills/x-video-skill
```

### 安装 Python 依赖

```bash
# TTS 配音（免费引擎）
pip3 install edge-tts

# 火山引擎 SDK（AI 生图）
pip3 install volcengine-python-sdk

# HTTP 请求（豆包 TTS / 下载媒体）
pip3 install requests

# Excel 导出
pip3 install openpyxl

# 海螺AI 视频生成（浏览器自动化，可选）
pip3 install playwright
```

或一条命令安装全部：

```bash
pip3 install edge-tts volcengine-python-sdk requests openpyxl playwright
```

### 验证安装

在 Claude Code 中输入以下任意指令，如果 Skill 被正确加载，Claude 会自动进入漫剧工作流：

```
漫剧:剧本
漫剧:全流程
帮我做一个AI漫剧
```

## 快速开始

### 方式一：全流程对话（推荐新手）

直接在 Claude Code 中描述你的想法：

```
帮我做一个AI漫剧，故事是：2035年AI接管了世界，一个程序员用最后一段代码重启了人类文明。科幻风格，1分钟短视频，横屏16:9。
```

Claude 会引导你完成全部 8 个阶段。

### 方式二：分步执行

```
漫剧:剧本      # 先写剧本
漫剧:美术      # 设计风格
漫剧:分镜      # 拆分镜
漫剧:文生图    # 生成图片提示词
漫剧:生图      # 调用AI生成图片
漫剧:生视频    # 调用AI生成视频
漫剧:后期      # 配音+音效+字幕+合成
```

## 配置

### 火山引擎 AK/SK（AI 生图/生视频）

```bash
# 方式一：交互式配置
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_image.py --setup

# 方式二：环境变量
export VOLCENGINE_AK="your_access_key"
export VOLCENGINE_SK="your_secret_key"
```

获取 AK/SK：https://console.volcengine.com/iam/keymanage

### 海螺AI 视频生成（可选，另一种生视频引擎）

海螺AI通过浏览器自动化操作，无需 API Key。需要：

1. 以调试模式启动 Chrome：
   ```bash
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
   ```

2. 在浏览器中手动登录海螺AI：https://hailuoai.com

3. 使用时添加 `--engine hailuo` 参数：
   ```bash
   # 单个生成
   python3 ~/.claude/skills/ai-comic-drama/scripts/generate_video.py \
     --engine hailuo --image first.png --prompt "角色转身" --output video.mp4

   # 批量生成（顺序执行，每条约2-3分钟）
   python3 ~/.claude/skills/ai-comic-drama/scripts/generate_video.py \
     --engine hailuo --file 分镜数据.json --image-dir ./images --output ./videos
   ```

### 豆包 TTS（可选，高质量配音）

编辑 `~/.ai-comic-drama/config.json`：

```json
{
  "doubao_tts_api_key": "your_doubao_api_key",
  "doubao_tts_resource_id": "seed-tts-2.0"
}
```

不配置则使用免费的 edge-tts 引擎。

### 音效库（可选）

```bash
# 初始化音效库目录
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_sfx.py --init-library

# 将 WAV/MP3 音效文件放入对应目录
# ~/.ai-comic-drama/sfx_library/
#   ambient/    环境音
#   mechanical/ 机械音
#   electronic/ 科技音
#   action/     动作音
#   climax/     高潮/结局音
```

## 工作区结构

每个漫剧项目使用独立的工作区目录：

```
我的项目/
├── 分镜数据.json              # 分镜脚本数据
├── 分镜表.xlsx                # Excel 交付物
├── output_images/             # AI 生成的图片
│   ├── 三视图_角色A.png
│   ├── 镜头001_首帧.png
│   └── ...
├── output_videos/             # AI 生成的视频 + 最终成片
│   ├── 镜头001.mp4
│   └── 最终成片.mp4
└── output_audio/
    ├── tts/                   # TTS 台词配音
    │   ├── 镜头001_台词.mp3
    │   └── 生成报告.json
    ├── sfx/                   # 音效
    │   ├── 镜头001_音效.mp3
    │   └── 生成报告.json
    ├── subtitle.srt           # 字幕
    └── bgm.mp3                # BGM
```

## 脚本工具

所有脚本支持 `--workspace` 指定项目目录，也可独立运行：

```bash
# TTS 配音（免费引擎）
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_tts.py \
  --file 分镜数据.json --workspace ./我的项目/ --engine free

# TTS 配音（豆包引擎，音质更好）
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_tts.py \
  --file 分镜数据.json --workspace ./我的项目/ --engine doubao

# 音效生成
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_sfx.py \
  --file 分镜数据.json --workspace ./我的项目/

# 字幕生成
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_subtitle.py \
  --file 分镜数据.json --workspace ./我的项目/

# 视频合成（自动 Doctor 检查 + 音频变速适配）
python3 ~/.claude/skills/ai-comic-drama/scripts/assemble_video.py \
  --file 分镜数据.json --workspace ./我的项目/

# AI 生图
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_image.py \
  --file 分镜数据.json --workspace ./我的项目/ --aspect 16:9

# AI 生视频
python3 ~/.claude/skills/ai-comic-drama/scripts/generate_video.py \
  --file 分镜数据.json --workspace ./我的项目/ --concurrency 2

# 导出 Excel
python3 ~/.claude/skills/ai-comic-drama/scripts/export_storyboard.py \
  --data '<分镜JSON>' --output 分镜表.xlsx
```

## 项目结构

```
ai-comic-drama/
├── SKILL.md                        # Skill 定义文件（Claude Code 自动加载）
├── README.md                       # 本文件
├── .gitignore
├── scripts/                        # 工具脚本
│   ├── workspace.py                #   工作区路径管理
│   ├── generate_image.py           #   火山引擎即梦AI 文生图
│   ├── generate_video.py           #   图生视频（即梦AI / 海螺AI）
│   ├── generate_tts.py             #   TTS 配音（edge-tts / 豆包）
│   ├── generate_sfx.py             #   本地音效库混音
│   ├── generate_subtitle.py        #   SRT 字幕生成
│   ├── assemble_video.py           #   ffmpeg 视频合成（含 Doctor 检查）
│   └── export_storyboard.py        #   分镜数据导出 Excel
└── references/                     # 知识库
    ├── prompt-library.md           #   5000+ 提示词词汇库
    ├── prompt-formulas.md          #   提示词公式与模板
    ├── movie-styles.md             #   300+ 电影风格分类库
    ├── visual-style-guide.md       #   色彩/光影/构图知识
    ├── storyboard-templates.md     #   分镜结构与节奏模板
    └── examples.md                 #   嫦娥奔月/甄嬛传完整案例
```

## 卸载

```bash
rm -rf ~/.claude/skills/x-video-skill
```

## 许可证

MIT License
