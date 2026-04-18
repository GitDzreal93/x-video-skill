#!/usr/bin/env python3
"""
AI漫剧字幕生成脚本
从分镜 JSON 的台词和时长数据生成 SRT 字幕文件

功能:
1. 基本模式：--file 读取分镜JSON，按镜头时长计算字幕时间
2. 精确模式：--tts-dir 指定 TTS 音频目录，用实际音频时长精确定位

字幕格式: 【说话人】台词内容
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


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


def parse_duration(duration_str):
    """解析时长字符串（如 '3s', '2.5s'）为秒数"""
    try:
        return float(duration_str.replace("s", "").strip())
    except (ValueError, AttributeError):
        return 3.0


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


def format_srt_time(seconds):
    """格式化为 SRT 时间码: HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def generate_subtitle(data, tts_dir=None, output_path=None):
    """生成分镜字幕文件"""
    shots = data.get("shots", [])
    metadata = data.get("metadata", {})
    title = metadata.get("title", "未命名")

    entries = []
    current_time = 0.0
    index = 1

    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        duration = parse_duration(shot.get("duration", "3s"))
        dialogue = shot.get("dialogue", "-")

        parsed = parse_dialogue(dialogue)
        if parsed:
            speaker, emotion, text = parsed

            # 尝试用 TTS 音频的实际时长
            tts_duration = None
            if tts_dir:
                tts_dir = Path(tts_dir)
                for name in [f"镜头{shot_num:03d}_台词.mp3",
                             f"镜头{shot_num:03d}_台词.wav"]:
                    p = tts_dir / name
                    if p.exists():
                        tts_duration = get_audio_duration(p)
                        break

            if tts_duration and tts_duration < duration:
                # TTS 音频短于镜头，居中放置
                start_offset = (duration - tts_duration) / 2
                start_time = current_time + start_offset
                end_time = start_time + tts_duration
            else:
                # 使用镜头时长
                start_time = current_time
                end_time = current_time + duration

            entries.append({
                "index": index,
                "start": format_srt_time(start_time),
                "end": format_srt_time(end_time),
                "text": text,
                "shot": shot_num,
            })
            index += 1

        current_time += duration

    # 生成 SRT 内容
    srt_lines = []
    for entry in entries:
        srt_lines.append(str(entry["index"]))
        srt_lines.append(f"{entry['start']} --> {entry['end']}")
        srt_lines.append(entry["text"])
        srt_lines.append("")  # 空行分隔

    srt_content = "\n".join(srt_lines)

    # 输出
    if output_path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(srt_content)
        print(f"字幕已生成: {output_path}")
    else:
        print(srt_content)

    # 报告
    total_dur = current_time
    print(f"\n共 {len(entries)} 条字幕，视频总时长 {total_dur:.1f}s")

    return entries


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧字幕生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本模式（按镜头时长估算）
  python generate_subtitle.py --file 分镜表.json --output subtitle.srt

  # 精确模式（用 TTS 音频实际时长）
  python generate_subtitle.py --file 分镜表.json --tts-dir ./output_audio/tts/ --output subtitle.srt

  # 输出到终端
  python generate_subtitle.py --file 分镜表.json
        """,
    )

    parser.add_argument("--file", "-f", required=True,
                        help="分镜 JSON 文件路径")
    parser.add_argument("--data", help="分镜 JSON 字符串")
    parser.add_argument("--tts-dir", help="TTS 音频目录（精确计时）")
    parser.add_argument("--output", "-o", help="输出 SRT 文件路径")

    add_workspace_args(parser)

    args = parser.parse_args()
    ws = resolve_workspace(args)

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif args.data:
        data = json.loads(args.data)
    else:
        parser.print_help()
        print("\n错误: 需要 --file 或 --data")
        sys.exit(1)

    tts_dir = args.tts_dir or str(ws.tts_dir)
    output_path = args.output or str(ws.subtitle_file)

    generate_subtitle(data, tts_dir=tts_dir, output_path=output_path)


if __name__ == "__main__":
    main()
