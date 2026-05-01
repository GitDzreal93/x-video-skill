#!/usr/bin/env python3
"""
AI漫剧 TTS 台词配音脚本
支持两种 TTS 引擎:
1. edge-tts (免费): 微软神经网络语音
2. doubao (豆包): 火山引擎大模型语音合成，音质更好、更有感染力

功能:
1. 单句生成：--text 指定文本，--voice 指定角色
2. 批量生成：--file 读取分镜JSON，自动为所有台词生成配音
3. 查看可用声音：--list-voices

用法:
  # 免费模式 (edge-tts)
  python generate_tts.py --file 分镜数据.json --output ./tts/ --engine free

  # 豆包模式 (音质更好)
  python generate_tts.py --file 分镜数据.json --output ./tts/ --engine doubao
"""

import argparse
import asyncio
import base64
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


# ── 配置路径 ──────────────────────────────────────────────

CONFIG_PATH = Path.home() / ".x-video-skill" / "config.json"


def load_config():
    """加载配置文件"""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── 豆包 TTS 配置 ──────────────────────────────────────────

DOUBAO_TTS_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional"

# 豆包音色配置 (2.0 音色)
DOUBAO_VOICES = {
    "臻叔": {
        # 云舟 2.0 - 沉稳男声，适合讲故事，带沧桑感
        "voice": "zh_male_m191_uranus_bigtts",
        "emotion": "storytelling",
        "speech_rate": 0,
        "loudness_rate": 0,
    },
    "AI执政官": {
        # 高冷沉稳 2.0 - 冷漠机械感
        "voice": "zh_male_gaolengchenwen_uranus_bigtts",
        "emotion": "coldness",
        "speech_rate": -15,
        "loudness_rate": 0,
    },
    "default": {
        "voice": "zh_male_m191_uranus_bigtts",
        "emotion": "storytelling",
        "speech_rate": 0,
        "loudness_rate": 0,
    },
}

# ── edge-tts 配置 ──────────────────────────────────────────

VOICE_CONFIG = {
    "AI执政官": {
        "voice": "zh-CN-XiaoxiaoNeural",
        "rate": "-15%",
        "pitch": "-5Hz",
        "volume": "-10%",
    },
    "臻叔": {
        "default": {
            "voice": "zh-CN-YunjianNeural",
            "rate": "-10%",
            "pitch": "-3Hz",
            "volume": "+0%",
        },
        "温暖": {
            "voice": "zh-CN-YunjianNeural",
            "rate": "-5%",
            "pitch": "+0Hz",
            "volume": "+0%",
        },
        "低语": {
            "voice": "zh-CN-YunjianNeural",
            "rate": "-20%",
            "pitch": "-5Hz",
            "volume": "-5%",
        },
        "沙哑": {
            "voice": "zh-CN-YunjianNeural",
            "rate": "-15%",
            "pitch": "-5Hz",
            "volume": "-5%",
        },
    },
}

ZHENSHU_EMOTION_MAP = {
    "低语": "低语",
    "沙哑": "沙哑",
    "温暖": "温暖",
    "画外音": "default",
}


# ── 通用工具函数 ────────────────────────────────────────────

def parse_dialogue(dialogue_str):
    """解析台词字段，返回 (speaker, emotion, text) 或 None"""
    if not dialogue_str or dialogue_str.strip() == "-":
        return None

    m = re.match(r'^(.+?)（(.+?)）[：:]["""](.+?)["""]$', dialogue_str.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()

    m = re.match(r'^(.+?)[：:]["""](.+?)["""]$', dialogue_str.strip())
    if m:
        return m.group(1).strip(), "", m.group(2).strip()

    return None


def get_audio_duration(filepath):
    """用 ffprobe 获取音频时长（秒）"""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1",
             str(filepath)],
            capture_output=True, text=True, timeout=10,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


# ── 豆包 TTS 引擎 ──────────────────────────────────────────

def doubao_tts_generate(text, output_path, speaker="臻叔",
                        speech_rate=None, shot_label=""):
    """使用豆包大模型语音合成生成语音"""
    import requests

    config = load_config()
    api_key = config.get("doubao_tts_api_key", "")
    resource_id = config.get("doubao_tts_resource_id", "seed-tts-2.0")

    if not api_key:
        print(f"  [{shot_label}] 错误: 未配置豆包 TTS API Key")
        print(f"  请在 {CONFIG_PATH} 中设置 doubao_tts_api_key")
        return False, None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 获取音色参数
    voice_config = DOUBAO_VOICES.get(speaker, DOUBAO_VOICES["default"])
    if speech_rate is None:
        speech_rate = voice_config.get("speech_rate", 0)

    print(f"  [{shot_label}] 豆包TTS: {text[:40]}... "
          f"(voice={voice_config['voice']}, rate={speech_rate})")

    # 构建请求
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id,
        "X-Api-Request-Id": str(uuid.uuid4()),
    }

    payload = {
        "user": {
            "uid": "x-video-skill",
        },
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": text,
            "speaker": voice_config["voice"],
            "audio_params": {
                "format": "mp3",
                "sample_rate": 24000,
                "bit_rate": 128000,
                "speech_rate": speech_rate,
                "loudness_rate": voice_config.get("loudness_rate", 0),
            },
        },
    }

    # 设置情感 (仅当 voice_config 有 emotion 字段时)
    emotion = voice_config.get("emotion")
    if emotion:
        payload["req_params"]["audio_params"]["emotion"] = emotion

    try:
        # 使用流式请求
        session = requests.Session()
        response = session.post(
            DOUBAO_TTS_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=30,
        )

        if response.status_code != 200:
            print(f"  [{shot_label}] 豆包TTS HTTP错误: {response.status_code}")
            print(f"  {response.text[:500]}")
            return False, None

        # 收集流式响应中的音频数据
        # 响应格式: 逐行 JSON，每行包含 base64 音频数据
        audio_chunks = []
        for line in response.iter_lines(decode_unicode=False):
            if not line:
                continue
            # 解码为字符串
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            line = line.strip()
            if not line:
                continue
            try:
                chunk = json.loads(line)
                code = chunk.get("code", -1)

                if code == 0 and chunk.get("data"):
                    # base64 音频数据
                    audio_data = base64.b64decode(chunk["data"])
                    audio_chunks.append(audio_data)
                elif code == 20000000:
                    # 合成结束（成功）
                    break
                elif code != 0 and code != 20000000:
                    msg = chunk.get("message", "unknown error")
                    print(f"  [{shot_label}] 豆包TTS 错误: code={code}, "
                          f"msg={msg}")
                    return False, None
            except (json.JSONDecodeError, KeyError):
                # 忽略无法解析的行
                pass

        if not audio_chunks:
            print(f"  [{shot_label}] 豆包TTS: 未收到音频数据")
            return False, None

        # 合并音频数据并保存
        audio_bytes = b"".join(audio_chunks)
        with open(output_path, "wb") as f:
            f.write(audio_bytes)

        duration = get_audio_duration(output_path)
        size_kb = len(audio_bytes) / 1024
        dur_str = f"{duration:.2f}s" if duration else "unknown"
        print(f"  [{shot_label}] 已保存: {output_path.name} "
              f"({size_kb:.0f} KB, {dur_str})")
        return True, duration

    except Exception as e:
        print(f"  [{shot_label}] 豆包TTS 异常: {e}")
        return False, None


# ── edge-tts 引擎 ──────────────────────────────────────────

def get_voice_params(speaker, emotion=""):
    """获取 edge-tts 角色对应的 TTS 参数"""
    config = VOICE_CONFIG.get(speaker)
    if not config:
        return {
            "voice": "zh-CN-YunjianNeural",
            "rate": "-10%",
            "pitch": "-3Hz",
            "volume": "+0%",
        }

    if speaker == "臻叔" and isinstance(config.get("default"), dict):
        sub_key = "default"
        for keyword, mapped_key in ZHENSHU_EMOTION_MAP.items():
            if keyword in emotion:
                sub_key = mapped_key
                break
        return config[sub_key]

    return config


async def _edge_tts_generate(text, voice, rate, pitch, volume, output_path):
    """调用 edge-tts 生成语音"""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch,
                                       volume=volume)
    await communicate.save(str(output_path))


def edge_tts_generate(text, output_path, voice="zh-CN-YunjianNeural",
                      rate="-10%", pitch="-3Hz", volume="+0%",
                      shot_label=""):
    """使用 edge-tts 生成单条语音"""
    print(f"  [{shot_label}] edge-tts: {text[:40]}... "
          f"(voice={voice}, rate={rate})")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    asyncio.run(_edge_tts_generate(text, voice, rate, pitch, volume,
                                   output_path))

    if not output_path.exists():
        print(f"  [{shot_label}] 生成失败")
        return False, None

    duration = get_audio_duration(output_path)
    size_kb = output_path.stat().st_size / 1024
    dur_str = f"{duration:.2f}s" if duration else "unknown"
    print(f"  [{shot_label}] 已保存: {output_path.name} "
          f"({size_kb:.0f} KB, {dur_str})")
    return True, duration


# ── 统一 TTS 接口 ──────────────────────────────────────────

def generate_tts(text, output_path, engine="free", speaker="臻叔",
                 speech_rate=None, shot_label=""):
    """生成单条 TTS 语音（统一接口）"""
    if engine == "doubao":
        return doubao_tts_generate(text, output_path, speaker=speaker,
                                   speech_rate=speech_rate,
                                   shot_label=shot_label)
    else:
        params = get_voice_params(speaker)
        return edge_tts_generate(
            text=text,
            output_path=output_path,
            voice=params["voice"],
            rate=params["rate"],
            pitch=params["pitch"],
            volume=params["volume"],
            shot_label=shot_label,
        )


# ── 批量生成 ──────────────────────────────────────────────

def batch_generate(data, output_dir, engine="free"):
    """从分镜 JSON 批量生成 TTS 台词配音"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shots = data.get("shots", [])
    metadata = data.get("metadata", {})
    results = []

    # 筛选有台词的镜头
    dialogue_shots = []
    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        dialogue = shot.get("dialogue", "-")
        parsed = parse_dialogue(dialogue)
        if parsed:
            dialogue_shots.append((shot_num, parsed))

    engine_label = "豆包TTS" if engine == "doubao" else "edge-tts"
    print(f"\n{'='*50}")
    print(f"TTS 台词配音 - {engine_label} (共{len(dialogue_shots)}条)")
    print(f"{'='*50}")

    for idx, (shot_num, (speaker, emotion, text)) in \
            enumerate(dialogue_shots):
        shot_label = f"{idx+1}/{len(dialogue_shots)} 镜头{shot_num:03d}"
        out_file = output_dir / f"镜头{shot_num:03d}_台词.mp3"

        # 跳过已存在的文件
        if out_file.exists():
            duration = get_audio_duration(out_file)
            print(f"  [{shot_label}] 已存在，跳过 "
                  f"({duration:.2f}s)" if duration else "")
            results.append({
                "shot": shot_num,
                "speaker": speaker,
                "emotion": emotion,
                "text": text,
                "file": str(out_file),
                "duration": duration,
                "success": True,
                "skipped": True,
            })
            continue

        success, duration = generate_tts(
            text=text,
            output_path=out_file,
            engine=engine,
            speaker=speaker,
            shot_label=shot_label,
        )

        results.append({
            "shot": shot_num,
            "speaker": speaker,
            "emotion": emotion,
            "text": text,
            "file": str(out_file) if success else None,
            "duration": duration,
            "success": success,
        })

    # 输出报告
    print(f"\n{'='*50}")
    print(f"TTS 生成完成 [{engine_label}]："
          f"{metadata.get('title', '未命名')}")
    print(f"{'='*50}")

    success_count = sum(1 for r in results if r["success"])
    print(f"总计: {success_count}/{len(results)} 成功")
    print(f"输出目录: {output_dir}")

    report_file = output_dir / "生成报告.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"报告: {report_file}")

    return results


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧 TTS 台词配音工具 (支持 edge-tts / 豆包)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 查看可用声音
  python generate_tts.py --list-voices

  # 免费模式批量生成 (edge-tts)
  python generate_tts.py --file 分镜表.json --output ./output_audio/tts/ --engine free

  # 豆包模式批量生成 (音质更好)
  python generate_tts.py --file 分镜表.json --output ./output_audio/tts/ --engine doubao

  # 使用工作区
  python generate_tts.py --file 分镜表.json --workspace /Volumes/dz/ai_video/我的项目/ --engine doubao

  # 单句生成
  python generate_tts.py --text "人类文明已由我接管" --voice zhenshu --output 台词.mp3 --engine doubao

  # 重新生成（删除已有文件）
  python generate_tts.py --file 分镜表.json --output ./output_audio/tts/ --engine doubao --force
        """,
    )

    # 操作模式
    parser.add_argument("--list-voices", action="store_true",
                        help="列出可用的声音")
    parser.add_argument("--setup", action="store_true",
                        help="初始化（安装依赖）")

    # 引擎选择
    parser.add_argument("--engine", choices=["free", "doubao"],
                        default="free",
                        help="TTS引擎: free=edge-tts(免费), "
                             "doubao=豆包(音质更好, 需API Key)")

    # 单句模式参数
    parser.add_argument("--text", help="台词文本")
    parser.add_argument("--output", "-o", help="输出文件路径")
    parser.add_argument("--voice", choices=["ai", "zhenshu", "default"],
                        default="default",
                        help="角色声音: ai(AI执政官), zhenshu(臻叔), "
                             "default(默认)")

    # 语音参数微调
    parser.add_argument("--speed", type=int, default=None,
                        help="语速调整 (-50~100, 豆包模式)")

    # 批量模式参数
    parser.add_argument("--file", "-f", help="分镜 JSON 文件路径（批量模式）")
    parser.add_argument("--data", help="分镜 JSON 字符串（批量模式）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新生成（忽略已存在的文件）")

    add_workspace_args(parser)

    args = parser.parse_args()
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    # 列出声音
    if args.list_voices:
        print("=== 豆包 TTS (doubao) 推荐音色 ===")
        print("  臻叔:     深夜播客 2.0 (zh_male_shenyeboke_uranus_bigtts)")
        print("            磁性解说 2.0 (zh_male_cixingjieshuonan_uranus_bigtts)")
        print("            高冷沉稳 2.0 (zh_male_gaolengchenwen_uranus_bigtts)")
        print("  AI执政官: 高冷沉稳 2.0 (zh_male_gaolengchenwen_uranus_bigtts)")
        print()
        print("=== edge-tts (free) 可用中文声音 ===")
        print("  AI执政官: zh-CN-XiaoxiaoNeural (女声，可调冰冷)")
        print("  臻叔:     zh-CN-YunjianNeural (男声，深沉)")
        print("  备选女声: zh-CN-XiaoyiNeural, zh-CN-XiaohanNeural")
        print("  备选男声: zh-CN-YunxiNeural, zh-CN-YunyangNeural")
        print()
        print("运行以下命令查看 edge-tts 完整列表：")
        print("  edge-tts --list-voices | grep zh-CN")
        return

    # setup 模式
    if args.setup:
        try:
            import edge_tts
            print("edge-tts 已安装")
        except ImportError:
            print("正在安装 edge-tts...")
            os.system("pip3 install edge-tts")
        try:
            import requests
            print("requests 已安装")
        except ImportError:
            print("正在安装 requests...")
            os.system("pip3 install requests")
        return

    # 批量模式
    if args.file or args.data:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(args.data)

        output_dir = args.output or str(ws.tts_dir)

        # --force 模式：删除已有文件以便重新生成
        if args.force and Path(output_dir).exists():
            for f in Path(output_dir).glob("镜头*_台词.mp3"):
                f.unlink()
                print(f"  已删除: {f.name}")

        batch_generate(data, output_dir, engine=args.engine)
        return

    # 单句模式
    if not args.text or not args.output:
        parser.print_help()
        print("\n错误: 需要 --text 和 --output，或使用 --file 批量模式")
        sys.exit(1)

    speaker_map = {
        "ai": "AI执政官",
        "zhenshu": "臻叔",
        "default": "臻叔",
    }
    speaker = speaker_map[args.voice]

    success, _ = generate_tts(
        text=args.text,
        output_path=args.output,
        engine=args.engine,
        speaker=speaker,
        speech_rate=args.speed,
    )

    if success:
        print(f"\n完成: {args.output}")
    else:
        print("\n生成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
