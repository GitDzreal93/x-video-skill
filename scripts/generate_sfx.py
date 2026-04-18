#!/usr/bin/env python3
"""
AI漫剧音效生成脚本
从分镜 JSON 的音效描述生成每镜头的音效音频

方案:
1. 本地音效库：~/.ai-comic-drama/sfx_library/ 中的 WAV/MP3 文件
2. 关键词匹配：将中文音效描述映射到本地文件
3. ffmpeg 混音：叠加多个音效到指定时长

音效库目录结构:
  sfx_library/
    ambient/     雨声、风声、脚步声等环境音
    mechanical/  金属撞击、摩擦、吱呀声等机械音
    electronic/  电子音、蜂鸣、启动声等科技音
    action/      蒸汽、心跳、键盘、电源键等动作音
    climax/      爆炸、碎裂、钢琴、鸟鸣等高潮/结局音

使用 --init-library 创建目录结构
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


# ── 音效库路径 ────────────────────────────────────────────

SFX_LIB_DIR = Path.home() / ".ai-comic-drama" / "sfx_library"

SFX_CATEGORIES = {
    "ambient": "环境音（雨声、风声、脚步声等）",
    "mechanical": "机械音（金属撞击、摩擦、吱呀声等）",
    "electronic": "科技音（电子音、蜂鸣、启动声等）",
    "action": "动作音（蒸汽、心跳、键盘、电源键等）",
    "climax": "高潮/结局音（爆炸、碎裂、钢琴、鸟鸣等）",
}

# ── 中文音效关键词 → 文件映射 ─────────────────────────────

# 格式: (关键词, 子目录, 文件名)
SFX_KEY_MAP = {
    # ambient
    "雨声": ("ambient", "rain.wav"),
    "雨": ("ambient", "rain.wav"),
    "雨滴": ("ambient", "rain_drops.wav"),
    "风声": ("ambient", "wind.wav"),
    "风": ("ambient", "wind.wav"),
    "脚步声": ("ambient", "footsteps.wav"),
    "脚步": ("ambient", "footsteps.wav"),
    "脚步回响": ("ambient", "footsteps_echo.wav"),
    "喘息声": ("ambient", "breathing.wav"),
    "急促呼吸": ("ambient", "breathing_fast.wav"),
    "鸟鸣": ("ambient", "birdsong.wav"),
    "鸟叫": ("ambient", "birdsong.wav"),

    # mechanical
    "金属撞击": ("mechanical", "metal_hit.wav"),
    "金属碰撞": ("mechanical", "metal_hit.wav"),
    "金属刮擦": ("mechanical", "metal_scrape.wav"),
    "铁门": ("mechanical", "door_creak.wav"),
    "吱呀": ("mechanical", "door_creak.wav"),
    "井盖": ("mechanical", "manhole.wav"),
    "拉链": ("mechanical", "zipper.wav"),
    "纸张": ("mechanical", "paper.wav"),
    "沙沙": ("mechanical", "paper.wav"),

    # electronic
    "电子音": ("electronic", "electronic_hum.wav"),
    "电子乐": ("electronic", "electronic_music.wav"),
    "蜂鸣": ("electronic", "buzzer.wav"),
    "拒绝声": ("electronic", "buzzer.wav"),
    "嗡鸣": ("electronic", "hum.wav"),
    "低频嗡鸣": ("electronic", "low_hum.wav"),
    "电脑启动": ("electronic", "computer_boot.wav"),
    "屏幕启动": ("electronic", "computer_boot.wav"),
    "风扇": ("electronic", "fan.wav"),
    "静音": ("electronic", "silence.wav"),

    # action
    "蒸汽": ("action", "steam_hiss.wav"),
    "嘶嘶": ("action", "steam_hiss.wav"),
    "心跳": ("action", "heartbeat.wav"),
    "键盘": ("action", "keyboard.wav"),
    "键盘敲击": ("action", "keyboard.wav"),
    "电源键": ("action", "power_click.wav"),
    "咔嗒": ("action", "click.wav"),
    "电源": ("action", "power_click.wav"),
    "擦灰": ("action", "wipe.wav"),
    "液体": ("action", "liquid_pour.wav"),

    # climax
    "爆炸": ("climax", "explosion.wav"),
    "爆裂": ("climax", "explosion.wav"),
    "碎裂": ("climax", "glass_break.wav"),
    "钢琴": ("climax", "piano.wav"),
    "轰鸣": ("climax", "boom.wav"),
}


def parse_duration(duration_str):
    """解析时长字符串为秒数"""
    try:
        return float(duration_str.replace("s", "").strip())
    except (ValueError, AttributeError):
        return 3.0


def parse_sound_tags(sound_str):
    """将音效描述拆分为标签列表"""
    if not sound_str:
        return []
    # 按 + 分割，清理空白
    tags = [t.strip() for t in sound_str.replace("，", "+").split("+")]
    return [t for t in tags if t]


def find_sfx_file(tag):
    """根据标签查找音效文件"""
    for keyword, (category, filename) in SFX_KEY_MAP.items():
        if keyword in tag:
            filepath = SFX_LIB_DIR / category / filename
            if filepath.exists():
                return filepath
    return None


def get_audio_duration(filepath):
    """用 ffprobe 获取音频时长"""
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


def generate_silent(duration, output_path):
    """用 ffmpeg 生成指定时长的静音音频"""
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


def mix_sfx_files(file_list, duration, output_path, shot_label=""):
    """用 ffmpeg 混合多个音效文件到指定时长"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not file_list:
        # 无音效文件，生成静音
        generate_silent(duration, output_path)
        return True

    if len(file_list) == 1:
        # 单个音效，截取/循环到指定时长
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",  # 循环输入
            "-i", str(file_list[0]),
            "-t", str(duration),
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        return result.returncode == 0

    # 多个音效，混合叠加
    # 先将每个音效循环到目标时长
    temp_files = []
    for i, f in enumerate(file_list):
        temp = output_path.parent / f"_temp_{i}_{output_path.name}"
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(f),
            "-t", str(duration),
            "-c:a", "pcm_s16le",
            str(temp),
        ]
        subprocess.run(cmd, capture_output=True, timeout=30)
        temp_files.append(temp)

    # 构造 amix 滤镜
    inputs = []
    filter_parts = []
    for i, temp in enumerate(temp_files):
        inputs.extend(["-i", str(temp)])
        filter_parts.append(f"[{i}:a]")

    amix_filter = "".join(filter_parts) + \
        f"amix=inputs={len(temp_files)}:duration=longest[aout]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", amix_filter,
        "-map", "[aout]",
        "-t", str(duration),
        "-c:a", "libmp3lame", "-b:a", "128k",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=30)

    # 清理临时文件
    for temp in temp_files:
        temp.unlink(missing_ok=True)

    return result.returncode == 0


# ── 批量生成 ──────────────────────────────────────────────

def batch_generate(data, output_dir):
    """从分镜 JSON 批量生成音效"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shots = data.get("shots", [])
    metadata = data.get("metadata", {})
    results = []

    print(f"\n{'='*50}")
    print(f"音效生成 (共{len(shots)}个镜头)")
    print(f"音效库: {SFX_LIB_DIR}")
    print(f"{'='*50}")

    missing_tags = set()

    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        sound = shot.get("sound", "")
        duration = parse_duration(shot.get("duration", "3s"))

        shot_label = f"{i+1}/{len(shots)} 镜头{shot_num:03d}"
        out_file = output_dir / f"镜头{shot_num:03d}_音效.mp3"

        # 跳过已存在的文件
        if out_file.exists():
            dur = get_audio_duration(out_file)
            print(f"  [{shot_label}] 已存在，跳过 "
                  f"({dur:.1f}s)" if dur else "")
            results.append({
                "shot": shot_num,
                "sound": sound,
                "file": str(out_file),
                "success": True,
                "skipped": True,
            })
            continue

        # 解析音效标签
        tags = parse_sound_tags(sound)
        if not tags:
            # 无音效描述，生成静音
            generate_silent(duration, out_file)
            print(f"  [{shot_label}] 无音效描述，生成静音 "
                  f"({duration:.1f}s)")
            results.append({
                "shot": shot_num,
                "sound": sound,
                "file": str(out_file),
                "success": True,
            })
            continue

        # 查找匹配的音效文件
        sfx_files = []
        found_tags = []
        for tag in tags:
            filepath = find_sfx_file(tag)
            if filepath:
                sfx_files.append(filepath)
                found_tags.append(tag)
            else:
                missing_tags.add(tag)

        if sfx_files:
            success = mix_sfx_files(sfx_files, duration, out_file,
                                    shot_label)
            found_str = "+".join(found_tags)
            missing_str = "+".join(
                t for t in tags if t not in found_tags)
            status = "OK" if success else "FAILED"
            print(f"  [{shot_label}] [{status}] {found_str}"
                  f"{' (缺少: ' + missing_str + ')' if missing_str else ''}"
                  f" ({duration:.1f}s)")
        else:
            generate_silent(duration, out_file)
            print(f"  [{shot_label}] [MISSING] 无匹配音效: "
                  f"{'+'.join(tags)}，使用静音")

        results.append({
            "shot": shot_num,
            "sound": sound,
            "file": str(out_file),
            "success": True,
        })

    # 报告
    print(f"\n{'='*50}")
    print(f"音效生成完成：{metadata.get('title', '未命名')}")
    print(f"{'='*50}")

    success_count = sum(1 for r in results if r["success"])
    print(f"总计: {success_count}/{len(results)} 成功")
    print(f"输出目录: {output_dir}")

    if missing_tags:
        print(f"\n未匹配的音效标签（{len(missing_tags)}个）:")
        for tag in sorted(missing_tags):
            print(f"  - {tag}")
        print(f"\n请将对应音效文件放入: {SFX_LIB_DIR}/")

    report_file = output_dir / "生成报告.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"报告: {report_file}")

    return results


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧音效生成工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 初始化音效库目录
  python generate_sfx.py --init-library

  # 从分镜JSON批量生成
  python generate_sfx.py --file 分镜表.json --output ./output_audio/sfx/

  # 单条生成
  python generate_sfx.py --sound "雨声+脚步声" --duration 3 --output sfx.mp3
        """,
    )

    parser.add_argument("--init-library", action="store_true",
                        help="创建音效库目录结构")

    # 单条模式
    parser.add_argument("--sound", help="音效描述（如 '雨声+脚步声'）")
    parser.add_argument("--duration", type=float, default=3.0,
                        help="音效时长（秒）")
    parser.add_argument("--output", "-o", help="输出文件路径")

    # 批量模式
    parser.add_argument("--file", "-f", help="分镜 JSON 文件路径")
    parser.add_argument("--data", help="分镜 JSON 字符串")

    add_workspace_args(parser)

    args = parser.parse_args()
    ws = resolve_workspace(args)

    # 初始化音效库
    if args.init_library:
        print("创建音效库目录结构...")
        SFX_LIB_DIR.mkdir(parents=True, exist_ok=True)
        for cat, desc in SFX_CATEGORIES.items():
            cat_dir = SFX_LIB_DIR / cat
            cat_dir.mkdir(exist_ok=True)
            # 创建 README
            readme = cat_dir / "README.txt"
            if not readme.exists():
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(f"音效分类: {cat}\n")
                    f.write(f"描述: {desc}\n\n")
                    f.write("请将对应的 WAV/MP3 音效文件放入此目录。\n")
            print(f"  {cat}/ ({desc})")
        print(f"\n音效库已创建: {SFX_LIB_DIR}")
        print("请将音效文件放入对应目录后重新运行。")
        return

    # 批量模式
    if args.file or args.data:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(args.data)

        output_dir = args.output or str(ws.sfx_dir)
        batch_generate(data, output_dir)
        return

    # 单条模式
    if args.sound and args.output:
        tags = parse_sound_tags(args.sound)
        sfx_files = [find_sfx_file(t) for t in tags]
        sfx_files = [f for f in sfx_files if f is not None]

        if sfx_files:
            success = mix_sfx_files(sfx_files, args.duration, args.output)
            if success:
                print(f"完成: {args.output}")
            else:
                print("生成失败")
                sys.exit(1)
        else:
            generate_silent(args.duration, args.output)
            print(f"无匹配音效，已生成静音: {args.output}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
