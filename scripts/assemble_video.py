#!/usr/bin/env python3
"""
AI漫剧视频合成脚本
将分镜视频片段、TTS配音、音效、BGM、字幕合成为最终成片

流程:
0. Doctor 检查：验证素材完整性、时长匹配，自动修复音频过长问题
1. 拼接视频片段（按分镜顺序，使用实际视频时长）
2. 混合音轨（TTS变速对齐 + 音效 + BGM 到正确时间偏移）
3. 合并视频 + 音频 + 字幕输出最终成片

依赖: ffmpeg
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


def parse_duration(duration_str):
    """解析时长字符串为秒数"""
    try:
        return float(duration_str.replace("s", "").strip())
    except (ValueError, AttributeError):
        return 3.0


def get_media_duration(filepath):
    """用 ffprobe 获取媒体时长（秒）"""
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


def run_ffmpeg(cmd, label=""):
    """运行 ffmpeg 命令"""
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        print(f"  [{label}] ffmpeg 错误: {result.stderr[-500:]}")
        return False
    return True


# ── Doctor 检查 ──────────────────────────────────────────

def doctor_check(shot_info, tts_dir=None, sfx_dir=None,
                 subtitle_path=None, bgm_path=None):
    """
    素材完整性检查 + 时长匹配检查。
    返回 (passed, issues, fixes)。
    passed: 是否全部通过（无致命错误）
    issues: 问题列表 [(级别, 消息)]
    fixes: 修复动作列表 [(镜头号, 类型, 详情)]
    """
    issues = []
    fixes = []
    passed = True

    # ── 1. 视频片段检查 ──
    vid_durs = []
    missing_videos = []
    for info in shot_info:
        vf = info["video_file"]
        if not vf.exists():
            missing_videos.append(vf.name)
            issues.append(("FATAL", f"视频缺失: {vf.name}"))
            passed = False
            continue
        dur = get_media_duration(vf)
        if dur is None or dur < 0.1:
            issues.append(("FATAL", f"视频损坏: {vf.name} (无法读取时长)"))
            passed = False
            continue
        info["video_duration"] = dur
        vid_durs.append(dur)

    if not vid_durs:
        return passed, issues, fixes

    # 检查视频时长一致性
    avg_dur = sum(vid_durs) / len(vid_durs)
    outlier_videos = []
    for info in shot_info:
        dur = info.get("video_duration", 0)
        if abs(dur - avg_dur) > 1.0:
            outlier_videos.append(info["shot_num"])
            issues.append(("WARN",
                f"镜头{info['shot_num']:03d} 时长异常: {dur:.2f}s "
                f"(平均{avg_dur:.2f}s)"))

    if outlier_videos:
        issues.append(("WARN",
            f"有{len(outlier_videos)}个镜头时长偏离平均，"
            f"将使用各自实际时长"))

    # ── 2. TTS 配音检查 ──
    if tts_dir:
        tts_dir = Path(tts_dir)
        for info in shot_info:
            num = info["shot_num"]
            tf = tts_dir / f"镜头{num:03d}_台词.mp3"
            if not tf.exists():
                issues.append(("WARN", f"镜头{num:03d}: 无TTS配音"))
                continue
            tts_dur = get_media_duration(tf)
            if tts_dur is None or tts_dur < 0.05:
                issues.append(("FATAL",
                    f"镜头{num:03d}: TTS音频损坏 ({tf.name})"))
                passed = False
                continue

            vid_dur = info.get("video_duration", 0)
            info["tts_file"] = tf
            info["tts_duration"] = tts_dur

            if tts_dur > vid_dur:
                # 需要加速: tempo = audio_dur / video_dur
                tempo = tts_dur / vid_dur
                fixes.append((num, "TTS加速",
                    f"{tts_dur:.2f}s → {vid_dur:.2f}s "
                    f"(加速 {tempo:.3f}x)"))
                info["tts_tempo"] = tempo
            elif tts_dur < vid_dur - 0.1:
                fixes.append((num, "TTS补静音",
                    f"{tts_dur:.2f}s → {vid_dur:.2f}s "
                    f"(补 {(vid_dur-tts_dur):.2f}s静音)"))
                info["tts_tempo"] = None  # 不需要变速，补静音

    # ── 3. 音效检查 ──
    if sfx_dir:
        sfx_dir = Path(sfx_dir)
        sfx_missing = 0
        for info in shot_info:
            num = info["shot_num"]
            sf = sfx_dir / f"镜头{num:03d}_音效.mp3"
            if not sf.exists():
                sfx_missing += 1
                continue
            sfx_dur = get_media_duration(sf)
            if sfx_dur is None or sfx_dur < 0.05:
                issues.append(("WARN",
                    f"镜头{num:03d}: 音效损坏 ({sf.name})"))
                continue
            info["sfx_file"] = sf
            info["sfx_duration"] = sfx_dur

            vid_dur = info.get("video_duration", 0)
            if sfx_dur > vid_dur:
                tempo = sfx_dur / vid_dur
                fixes.append((num, "SFX加速",
                    f"{sfx_dur:.2f}s → {vid_dur:.2f}s "
                    f"(加速 {tempo:.3f}x)"))
                info["sfx_tempo"] = tempo

    # ── 4. 字幕检查 ──
    if subtitle_path and Path(subtitle_path).exists():
        content = Path(subtitle_path).read_text(encoding="utf-8")
        entry_count = content.count("-->")
        if entry_count < len(shot_info):
            issues.append(("WARN",
                f"字幕条目({entry_count})少于镜头数({len(shot_info)})"))
        if "【" in content and "】" in content:
            issues.append(("WARN",
                "字幕包含说话人标签【】，建议去除"))
    elif subtitle_path:
        issues.append(("WARN", f"字幕文件不存在: {subtitle_path}"))

    # ── 5. BGM 检查 ──
    if bgm_path and Path(bgm_path).exists():
        bgm_dur = get_media_duration(bgm_path)
        total = sum(info.get("video_duration", 0) for info in shot_info)
        if bgm_dur and bgm_dur < total * 0.5:
            issues.append(("WARN",
                f"BGM时长({bgm_dur:.1f}s)不到视频总长({total:.1f}s)一半，"
                f"将循环播放"))

    return passed, issues, fixes


def print_doctor_report(issues, fixes):
    """打印 doctor 检查报告"""
    print(f"\n{'─'*50}")
    print("📋 Doctor 检查报告")
    print(f"{'─'*50}")

    if not issues and not fixes:
        print("  ✓ 所有素材检查通过，无需调整")
        return

    fatal = [i for i in issues if i[0] == "FATAL"]
    warns = [i for i in issues if i[0] == "WARN"]

    if fatal:
        print(f"\n  ❌ 致命错误 ({len(fatal)}个):")
        for _, msg in fatal:
            print(f"     {msg}")

    if warns:
        print(f"\n  ⚠️  警告 ({len(warns)}个):")
        for _, msg in warns:
            print(f"     {msg}")

    if fixes:
        print(f"\n  🔧 自动修复 ({len(fixes)}项):")
        for num, fix_type, detail in fixes:
            print(f"     镜头{num:03d} [{fix_type}]: {detail}")

    print(f"{'─'*50}")


# ── 音频适配（变速 or 补静音）──────────────────────────

def fit_audio(src_path, target_dur, tempo=None, label=""):
    """
    将音频适配到目标时长:
    - tempo > 1: 加速播放（atempo），保留完整内容不截断
    - 无 tempo 且短于目标: 补静音到目标时长
    - 无 tempo 且等于目标: 原样返回
    """
    src_dur = get_media_duration(src_path)
    if src_dur is None:
        return src_path

    # 不需要调整
    if abs(src_dur - target_dur) < 0.05 and tempo is None:
        return src_path

    out = Path(tempfile.mktemp(suffix=".mp3"))

    if tempo and tempo > 1.0:
        # atempo 加速: 将完整音频压缩到目标时长
        # atempo 范围 [0.5, 100]，超过2.0需要链式处理
        if tempo <= 2.0:
            af = f"atempo={tempo:.4f}"
        else:
            # 链式 atempo (每个最大2.0)
            t1 = min(tempo, 2.0)
            t2 = tempo / t1
            if t2 <= 2.0:
                af = f"atempo={t1:.4f},atempo={t2:.4f}"
            else:
                t3 = t2 / 2.0
                af = f"atempo={t1:.4f},atempo=2.0,atempo={t3:.4f}"

        # 加速后精确截断到目标时长
        af += f",atrim=0:{target_dur}"

        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-af", af,
            "-t", str(target_dur),
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(out),
        ]
    else:
        # 音频短于目标：补静音
        cmd = [
            "ffmpeg", "-y",
            "-i", str(src_path),
            "-af", f"apad=whole_dur={target_dur}",
            "-t", str(target_dur),
            "-c:a", "libmp3lame", "-b:a", "128k",
            str(out),
        ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        print(f"  [{label}] 音频适配失败: {result.stderr[-200:]}")
        return src_path

    actual = get_media_duration(out)
    if actual:
        print(f"  [{label}] {src_dur:.2f}s → {actual:.2f}s "
              f"(目标{target_dur:.2f}s"
              f"{f', 加速{tempo:.3f}x' if tempo else ', 补静音'})")

    return out


# ── 主合成流程 ────────────────────────────────────────────

def assemble_video(data, video_dir, output_path, tts_dir=None,
                   sfx_dir=None, bgm_path=None, subtitle_path=None,
                   no_subtitle=False, no_bgm=False, skip_doctor=False):
    """主合成流程"""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    video_dir = Path(video_dir)

    shots = data.get("shots", [])
    metadata = data.get("metadata", {})
    title = metadata.get("title", "未命名")

    print(f"\n{'='*50}")
    print(f"视频合成：{title}")
    print(f"{'='*50}")

    # ── 收集镜头信息（使用实际视频时长）──
    shot_info = []
    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        video_file = video_dir / f"镜头{shot_num:03d}.mp4"
        shot_info.append({
            "shot_num": shot_num,
            "video_file": video_file,
        })

    # ── Step 0: Doctor 检查 ──
    if not skip_doctor:
        print(f"\n[Step 0/4] Doctor 检查...")
        passed, issues, fixes = doctor_check(
            shot_info, tts_dir=tts_dir, sfx_dir=sfx_dir,
            subtitle_path=subtitle_path if not no_subtitle else None,
            bgm_path=bgm_path if not no_bgm else None,
        )
        print_doctor_report(issues, fixes)

        if not passed:
            print("\n❌ Doctor 检查发现致命错误，无法继续合成。")
            print("   请修复以上标记为 FATAL 的问题后重试。")
            return False
    else:
        # 跳过 doctor 时仍然需要基本信息
        for info in shot_info:
            dur = get_media_duration(info["video_file"])
            info["video_duration"] = dur if dur else parse_duration("3s")
            if tts_dir:
                tf = Path(tts_dir) / f"镜头{info['shot_num']:03d}_台词.mp3"
                if tf.exists():
                    info["tts_file"] = tf
                    info["tts_duration"] = get_media_duration(tf) or 0

    # ── 计算时间轴（使用实际视频时长）──
    total_duration = 0.0
    for info in shot_info:
        vid_dur = info.get("video_duration", 0)
        info["start_time"] = total_duration
        total_duration += vid_dur

    print(f"\n  镜头数: {len(shots)}")
    print(f"  总时长: {total_duration:.1f}s")
    print(f"  字幕: {'有' if subtitle_path and not no_subtitle else '无'}")
    print(f"  BGM: {'有' if bgm_path and not no_bgm else '无'}")
    print(f"  TTS: {'有' if tts_dir else '无'}")
    print(f"  音效: {'有' if sfx_dir else '无'}")

    temp_files = []

    try:
        # ── Step 1: 音频适配 ──
        print(f"\n[Step 1/4] 适配音频...")

        # 适配 TTS
        if tts_dir:
            for info in shot_info:
                if "tts_file" not in info:
                    continue
                tf = info["tts_file"]
                vid_dur = info.get("video_duration", 0)
                tts_dur = info.get("tts_duration", 0)
                tempo = info.get("tts_tempo")

                if tempo or abs(tts_dur - vid_dur) > 0.1:
                    fitted = fit_audio(
                        tf, vid_dur, tempo=tempo,
                        label=f"TTS镜头{info['shot_num']:03d}")
                    if fitted != tf:
                        temp_files.append(fitted)
                    info["tts_fitted"] = fitted
                else:
                    info["tts_fitted"] = tf

        # 适配 SFX
        if sfx_dir:
            for info in shot_info:
                if "sfx_file" not in info:
                    continue
                sf = info["sfx_file"]
                vid_dur = info.get("video_duration", 0)
                tempo = info.get("sfx_tempo")

                if tempo:
                    fitted = fit_audio(
                        sf, vid_dur, tempo=tempo,
                        label=f"SFX镜头{info['shot_num']:03d}")
                    if fitted != sf:
                        temp_files.append(fitted)
                    info["sfx_fitted"] = fitted
                else:
                    info["sfx_fitted"] = sf

        # ── Step 2: 拼接视频 ──
        print(f"\n[Step 2/4] 拼接视频片段...")

        concat_file = Path(tempfile.mktemp(suffix=".txt"))
        temp_files.append(concat_file)

        with open(concat_file, "w", encoding="utf-8") as f:
            for info in shot_info:
                abs_path = str(info['video_file'].resolve())
                vid_dur = info.get("video_duration", 0)
                f.write(f"file '{abs_path}'\n")
                f.write(f"duration {vid_dur}\n")
            # concat demuxer 需要最后一行再写一次文件
            abs_last = str(shot_info[-1]['video_file'].resolve())
            f.write(f"file '{abs_last}'\n")

        concat_video = Path(tempfile.mktemp(suffix=".mp4"))
        temp_files.append(concat_video)

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-an",
            "-pix_fmt", "yuv420p",
            str(concat_video),
        ]
        if not run_ffmpeg(cmd, "拼接视频"):
            return False

        concat_dur = get_media_duration(concat_video)
        print(f"  拼接完成: {concat_dur:.1f}s" if concat_dur
              else "  拼接完成")

        # ── Step 3: 混合音轨 ──
        print(f"\n[Step 3/4] 混合音轨...")

        mixed_audio = Path(tempfile.mktemp(suffix=".wav"))
        temp_files.append(mixed_audio)

        audio_inputs = []
        filter_parts = []
        input_idx = 0

        # 基础静音轨道
        audio_inputs.extend([
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo",
        ])
        base_idx = input_idx
        input_idx += 1

        mix_inputs = [f"[{base_idx}:a]atrim=0:{total_duration}[a_base]"]
        filter_outputs = ["a_base"]

        # 添加音效（已适配到视频时长）
        if sfx_dir:
            for info in shot_info:
                sf = info.get("sfx_fitted")
                if sf and Path(str(sf)).exists():
                    audio_inputs.extend(["-i", str(sf)])
                    delay_ms = int(info["start_time"] * 1000)
                    label = f"a_sfx_{info['shot_num']}"
                    filter_parts.append(
                        f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}"
                        f",apad=whole_dur={total_duration}[{label}]"
                    )
                    filter_outputs.append(label)
                    input_idx += 1

        # 添加 TTS（已适配到视频时长）
        if tts_dir:
            for info in shot_info:
                tf = info.get("tts_fitted")
                if tf and Path(str(tf)).exists():
                    audio_inputs.extend(["-i", str(tf)])
                    delay_ms = int(info["start_time"] * 1000)
                    label = f"a_tts_{info['shot_num']}"
                    filter_parts.append(
                        f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}"
                        f",apad=whole_dur={total_duration}[{label}]"
                    )
                    filter_outputs.append(label)
                    input_idx += 1

        # 添加 BGM
        if bgm_path and not no_bgm:
            bgm_path = Path(bgm_path)
            if bgm_path.exists():
                audio_inputs.extend(["-i", str(bgm_path)])
                label = "a_bgm"
                fade_in = f"afade=t=in:st=0:d=2"
                fade_out = f"afade=t=out:st={total_duration-3}:d=3"
                filter_parts.append(
                    f"[{input_idx}:a]volume=0.3,{fade_in},{fade_out}"
                    f",apad=whole_dur={total_duration}[{label}]"
                )
                filter_outputs.append(label)
                input_idx += 1

        # 构建 amix 滤镜
        all_filter = ";".join(mix_inputs + filter_parts)
        mix_labels = "".join(f"[{l}]" for l in filter_outputs)
        amix = (f"{all_filter};{mix_labels}"
                f"amix=inputs={len(filter_outputs)}"
                f":duration=longest:dropout_transition=2[aout]")

        cmd = [
            "ffmpeg", "-y",
            *audio_inputs,
            "-filter_complex", amix,
            "-map", "[aout]",
            "-t", str(total_duration),
            "-c:a", "pcm_s16le",
            str(mixed_audio),
        ]
        if not run_ffmpeg(cmd, "混合音轨"):
            print("  音频混合失败，使用静音轨道")
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r=44100:cl=stereo",
                "-t", str(total_duration),
                "-c:a", "pcm_s16le",
                str(mixed_audio),
            ]
            run_ffmpeg(cmd, "生成静音")

        # ── Step 4: 合成最终视频 ──
        print(f"\n[Step 4/4] 合成最终视频...")

        if subtitle_path and not no_subtitle and Path(subtitle_path).exists():
            srt_escaped = str(Path(subtitle_path).resolve()).replace(
                ":", "\\:").replace("'", "\\'")

            cmd = [
                "ffmpeg", "-y",
                "-i", str(concat_video),
                "-i", str(mixed_audio),
                "-vf", f"subtitles='{srt_escaped}'"
                       f":force_style='FontSize=20,"
                       f"PrimaryColour=&H00FFFFFF,"
                       f"OutlineColour=&H00000000,"
                       f"Outline=2,Alignment=2,"
                       f"MarginV=30'",
                "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p",
                "-shortest",
                str(output_path),
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", str(concat_video),
                "-i", str(mixed_audio),
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest",
                str(output_path),
            ]

        if not run_ffmpeg(cmd, "合成"):
            return False

        # ── 输出结果 ──
        final_size = output_path.stat().st_size / 1024 / 1024
        final_dur = get_media_duration(output_path)

        print(f"\n{'='*50}")
        print(f"合成完成：{title}")
        print(f"{'='*50}")
        print(f"  输出: {output_path}")
        print(f"  时长: {final_dur:.1f}s" if final_dur else "")
        print(f"  大小: {final_size:.1f} MB")
        print(f"  字幕: {'已嵌入' if subtitle_path and not no_subtitle else '无'}")
        print(f"  BGM: {'已混合' if bgm_path and not no_bgm else '无'}")

        return True

    finally:
        for f in temp_files:
            f.unlink(missing_ok=True)


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧视频合成工具（含 Doctor 检查）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 完整合成（自动执行 Doctor 检查）
  python assemble_video.py --file 分镜数据.json \\
    --video-dir ./output_videos/ \\
    --tts-dir ./output_audio/tts/ \\
    --sfx-dir ./output_audio/sfx/ \\
    --subtitle ./output_audio/subtitle.srt \\
    --output ./output_videos/最终成片.mp4

  # 跳过 Doctor 检查
  python assemble_video.py --file 分镜数据.json \\
    --video-dir ./output_videos/ \\
    --tts-dir ./output_audio/tts/ \\
    --output ./output_videos/成片.mp4 \\
    --skip-doctor

  # 仅拼接视频（无音频）
  python assemble_video.py --file 分镜数据.json \\
    --video-dir ./output_videos/ \\
    --output ./output_videos/拼接.mp4 \\
    --no-bgm --no-subtitle

  # 使用工作区（自动设置所有输入输出路径）
  python assemble_video.py --file 分镜数据.json \\
    --workspace /Volumes/dz/ai_video/我的项目/ \\
    --engine doubao
        """,
    )

    parser.add_argument("--file", "-f", required=True,
                        help="分镜 JSON 文件路径")
    parser.add_argument("--data", help="分镜 JSON 字符串")
    parser.add_argument("--video-dir",
                        help="视频片段目录（默认: {workspace}/output_videos/）")
    parser.add_argument("--tts-dir", help="TTS 台词音频目录")
    parser.add_argument("--sfx-dir", help="音效音频目录")
    parser.add_argument("--bgm", help="BGM 音频文件路径")
    parser.add_argument("--subtitle", help="SRT 字幕文件路径")
    parser.add_argument("--output", "-o",
                        help="输出视频文件路径（默认: {workspace}/最终成片.mp4）")
    parser.add_argument("--no-subtitle", action="store_true",
                        help="不嵌入字幕")
    parser.add_argument("--no-bgm", action="store_true",
                        help="不混合 BGM")
    parser.add_argument("--skip-doctor", action="store_true",
                        help="跳过 Doctor 检查步骤")
    add_workspace_args(parser)

    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            data = json.load(f)
    elif args.data:
        data = json.loads(args.data)
    else:
        parser.print_help()
        print("\n错误: 需要 --file 或 --data")
        sys.exit(1)

    # 工作区路径解析
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    # 未指定的目录参数使用工作区默认值
    video_dir = args.video_dir or str(ws.videos_dir)
    tts_dir = args.tts_dir or str(ws.tts_dir)
    sfx_dir = args.sfx_dir or str(ws.sfx_dir)
    subtitle = args.subtitle or str(ws.subtitle_file)
    bgm = args.bgm or str(ws.bgm_file)
    output = args.output or str(ws.root / "最终成片.mp4")

    success = assemble_video(
        data,
        video_dir=video_dir,
        output_path=output,
        tts_dir=tts_dir,
        sfx_dir=sfx_dir,
        bgm_path=bgm,
        subtitle_path=subtitle,
        no_subtitle=args.no_subtitle,
        no_bgm=args.no_bgm,
        skip_doctor=args.skip_doctor,
    )

    if not success:
        print("\n合成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
