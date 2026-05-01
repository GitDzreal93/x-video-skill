#!/usr/bin/env python3
"""
AI漫剧全流程编排脚本

全链路串行执行 Phase 1-8，从故事创意到最终成片。
支持两种 AI 生成引擎：
  1. dreamina CLI（即梦官方CLI，推荐）— 无需API Key，需先登录
  2. 即梦AI API（generate_image.py / generate_video.py）— 需 AK/SK

用法:
  # 检查环境（推荐先运行）
  python3 pipeline.py doctor --workspace /path/to/project

  # 全流程一键执行
  python3 pipeline.py all --workspace /path/to/project --storyboard 分镜数据.json

  # 分步执行（各阶段独立）
  python3 pipeline.py generate-images  --workspace /path/to/project --storyboard 分镜数据.json
  python3 pipeline.py generate-videos  --workspace /path/to/project --storyboard 分镜数据.json
  python3 pipeline.py tts              --workspace /path/to/project --storyboard 分镜数据.json
  python3 pipeline.py sfx              --workspace /path/to/project --storyboard 分镜数据.json
  python3 pipeline.py subtitle         --workspace /path/to/project --storyboard 分镜数据.json
  python3 pipeline.py assemble         --workspace /path/to/project --storyboard 分镜数据.json

依赖:
  - ffmpeg (视频合成)
  - edge-tts (可选, TTS配音) 或 豆包 TTS
  - dreamina CLI (可选, 即梦官方CLI，推荐用于AI生图/生视频)
    安装: curl -s https://jimeng.jianying.com/cli | bash
  - Volcengine AK/SK (可选, 备选AI生图/生视频方案)
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


SCRIPT_DIR = Path(__file__).parent


# ── 工具函数 ────────────────────────────────────────────────

def print_header(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def run_script(script_name, args_list, cwd=None):
    """运行 scripts/ 下的某个 Python 脚本"""
    script = SCRIPT_DIR / script_name
    cmd = [sys.executable, str(script)] + args_list
    print(f"  $ {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    return result.returncode == 0


def run_cmd(cmd, label=""):
    """运行任意 shell 命令"""
    print(f"  [{label}] $ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=False)
    return result.returncode == 0


def which(binary):
    """检查命令是否存在"""
    return subprocess.run(["which", binary], capture_output=True).returncode == 0


# ── 依赖检查 ────────────────────────────────────────────────

def check_dreamina_cli():
    """检查 dreamina CLI 是否可用"""
    # dreamina 可能不在 PATH 中，检查常见位置
    for path in [
        "dreamina",
        str(Path.home() / ".local" / "bin" / "dreamina"),
        str(Path.home() / ".jimeng" / "cli" / "dreamina"),
    ]:
        try:
            result = subprocess.run(
                [path, "--version"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return path, result.stdout.strip()
        except (FileNotFoundError, PermissionError):
            continue
    return None, None


def doctor(args):
    """检查所有前置依赖"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    print_header("环境检查")

    all_ok = True

    # 1. ffmpeg
    ffmpeg_ok = which("ffmpeg")
    print(f"  ffmpeg:     {'✓' if ffmpeg_ok else '✗ 未安装 (brew install ffmpeg)'}")
    all_ok &= ffmpeg_ok

    # 2. ffprobe
    ffprobe_ok = which("ffprobe")
    print(f"  ffprobe:    {'✓' if ffprobe_ok else '✗ 未安装 (brew install ffmpeg)'}")
    all_ok &= ffprobe_ok

    # 3. dreamina CLI (即梦官方CLI)
    dreamina_path, dreamina_ver = check_dreamina_cli()
    if dreamina_path:
        print(f"  dreamina:   ✓ ({dreamina_ver})")
        # 检查登录状态
        try:
            credit_result = subprocess.run(
                [dreamina_path, "user_credit"],
                capture_output=True, text=True, timeout=10,
            )
            if credit_result.returncode == 0:
                print(f"  dreamina 登录: ✓ (积分可用)")
            else:
                print(f"  dreamina 登录: ✗ 未登录，请运行: {dreamina_path} login")
                all_ok = False
        except Exception:
            print(f"  dreamina 登录: ✗ 查询失败，请运行: {dreamina_path} login")
            all_ok = False
    else:
        print(f"  dreamina:   ✗ 未安装")
        print(f"              安装: curl -s https://jimeng.jianying.com/cli | bash")
        print(f"              登录: dreamina login")

    # 4. edge-tts (可选)
    edge_tts_ok = which("edge-tts")
    print(f"  edge-tts:   {'✓' if edge_tts_ok else '✗ 未安装 (pip install edge-tts, 可选)'}")

    # 5. TTS 脚本依赖
    try:
        import edge_tts
        print(f"  edge_tts (py): ✓")
    except ImportError:
        print(f"  edge_tts (py): ✗ pip install edge-tts")

    try:
        import requests
        print(f"  requests (py): ✓")
    except ImportError:
        print(f"  requests (py): ✗ pip install requests (可选, 用于豆包TTS)")

    # 6. 工作区
    print(f"  工作区:     {ws.root}")
    ws.ensure_dirs()
    print(f"  output_images/   ✓")
    print(f"  output_videos/   ✓")
    print(f"  output_audio/    ✓")

    print()
    if all_ok:
        print("  ✓ 所有核心依赖已就绪")
    else:
        print("  ⚠ 部分依赖缺失，请按提示安装")
    return all_ok


# ── 图片生成 ────────────────────────────────────────────────

def cmd_generate_images(args):
    """Phase 6a: 批量生成图片 (角色三视图 + 各镜头首帧/尾帧/代表画面)"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    data = load_storyboard(args)
    if not data:
        return False

    dreamina_path, _ = check_dreamina_cli()
    if dreamina_path:
        return _generate_images_dreamina(dreamina_path, data, ws, args)
    else:
        return _generate_images_legacy(data, ws, args)


def _generate_images_dreamina(dreamina_path, data, ws, args):
    """使用 dreamina CLI 生成图片"""
    print_header("AI 生图 (dreamina CLI)")

    characters = data.get("characters", [])
    shots = data.get("shots", [])

    aspect = getattr(args, "aspect", "16:9")
    ratio = aspect.replace("9:16", "9:16").replace("16:9", "16:9").replace("1:1", "1:1")

    results = []

    # 角色三视图
    for char in characters:
        name = char.get("name", "未知")
        prompt = char.get("turnaround_prompt", "")
        if not prompt:
            continue
        out = ws.images_dir / f"三视图_{name}.png"
        if out.exists() and not getattr(args, "force", False):
            print(f"  [三视图_{name}] 已存在，跳过")
            results.append({"name": name, "file": str(out), "success": True})
            continue
        print(f"  [三视图_{name}] 提交...")
        cmd = [dreamina_path, "text2image",
               "--prompt", prompt,
               "--ratio", "1:1",
               "--poll", "120"]
        ok = _run_dreamina_and_download(cmd, out, f"三视图_{name}")
        results.append({"name": name, "file": str(out) if ok else None, "success": ok})

    # 各镜头图片
    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        for img_type, key in [("首帧", "first_frame_prompt"),
                               ("尾帧", "last_frame_prompt"),
                               ("代表画面", "image_prompt")]:
            prompt = shot.get(key, "")
            if not prompt:
                continue
            out = ws.images_dir / f"镜头{shot_num:03d}_{img_type}.png"
            if out.exists() and not getattr(args, "force", False):
                print(f"  [镜头{shot_num:03d}_{img_type}] 已存在，跳过")
                results.append({"shot": shot_num, "type": img_type, "file": str(out), "success": True})
                continue
            print(f"  [镜头{shot_num:03d}_{img_type}] 提交...")
            cmd = [dreamina_path, "text2image",
                   "--prompt", prompt,
                   "--ratio", ratio,
                   "--poll", "120"]
            ok = _run_dreamina_and_download(cmd, out, f"镜头{shot_num:03d}_{img_type}")
            results.append({"shot": shot_num, "type": img_type, "file": str(out) if ok else None, "success": ok})

    _print_report(results, ws.images_dir)
    return True


def _run_dreamina_and_download(cmd, output_path, label):
    """运行 dreamina CLI 并下载结果"""
    # 提交任务
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        print(f"  [{label}] 提交失败: {result.stderr[:200]}")
        return False

    # 解析 submit_id
    try:
        for line in result.stdout.split("\n"):
            if "submit_id" in line.lower():
                import re
                m = re.search(r'[\'"]([a-f0-9-]+)[\'"]', line)
                if m:
                    sid = m.group(1)
                    break
        else:
            # 可能 poll 完成了，检查是否有下载的输出
            print(f"  [{label}] 可能已完成: {result.stdout[:200]}")
            # dreamina 有时把文件下载到当前目录，检查一下
            return _find_downloaded_file(label, output_path)
    except Exception:
        # 如果 poll 成功，响应中可能包含媒体
        print(f"  [{label}] 尝试解析结果...")
        return _find_downloaded_file(label, output_path)

    # poll 超时后手动查询并下载
    print(f"  [{label}] submit_id={sid}, 查询结果...")
    dl_dir = output_path.parent / ".dreamina_dl"
    dl_dir.mkdir(exist_ok=True)
    qr_cmd = [cmd[0], "query_result",
              "--submit_id", sid,
              "--download_dir", str(dl_dir)]
    for _ in range(30):  # 最多等 5 分钟
        r = subprocess.run(qr_cmd, capture_output=True, text=True, timeout=30)
        if r.returncode == 0:
            # 找下载的文件
            files = list(dl_dir.iterdir()) if dl_dir.exists() else []
            if files:
                import shutil
                src = max(files, key=lambda f: f.stat().st_mtime)
                shutil.move(str(src), str(output_path))
                print(f"  [{label}] 已保存: {output_path.name}")
                return True
        time.sleep(10)

    print(f"  [{label}] 超时")
    return False


def _find_downloaded_file(label, output_path):
    """查找 dreamina 可能已下载到当前目录的文件"""
    import glob
    for ext in [".png", ".jpg", ".jpeg", ".mp4"]:
        for f in sorted(Path.cwd().glob(f"*{ext}")):
            if f.stat().st_mtime > time.time() - 60:  # 最近1分钟内
                import shutil
                shutil.move(str(f), str(output_path))
                print(f"  [{label}] 已保存: {output_path.name}")
                return True
    return False


def _generate_images_legacy(data, ws, args):
    """回退方案：使用 generate_image.py (Volcengine API)"""
    print_header("AI 生图 (即梦API - 回退方案)")
    aspect = getattr(args, "aspect", "16:9")
    return run_script("generate_image.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--output", str(ws.images_dir),
        "--aspect", aspect,
        "--workspace", str(ws.root),
    ])


# ── 视频生成 ────────────────────────────────────────────────

def cmd_generate_videos(args):
    """Phase 6b: 批量图生视频"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    data = load_storyboard(args)
    if not data:
        return False

    dreamina_path, _ = check_dreamina_cli()
    if dreamina_path:
        return _generate_videos_dreamina(dreamina_path, data, ws, args)
    else:
        return _generate_videos_legacy(data, ws, args)


def _generate_videos_dreamina(dreamina_path, data, ws, args):
    """使用 dreamina CLI 图生视频"""
    print_header("AI 生视频 (dreamina CLI)")

    shots = data.get("shots", [])
    results = []

    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        video_prompt = shot.get("video_prompt", "")
        if not video_prompt:
            continue

        out = ws.videos_dir / f"镜头{shot_num:03d}.mp4"
        if out.exists() and not getattr(args, "force", False):
            print(f"  [镜头{shot_num:03d}] 已存在，跳过")
            results.append({"shot": shot_num, "file": str(out), "success": True})
            continue

        # 查找首帧
        first_frame = _find_image(ws.images_dir, f"镜头{shot_num:03d}_首帧")
        if not first_frame:
            first_frame = _find_image(ws.images_dir, f"镜头{shot_num:03d}_代表画面")

        # 查找尾帧
        last_frame = _find_image(ws.images_dir, f"镜头{shot_num:03d}_尾帧")

        if not first_frame:
            print(f"  [镜头{shot_num:03d}] 找不到首帧图片，跳过")
            continue

        print(f"  [镜头{shot_num:03d}] 提交...")
        if last_frame:
            cmd = [dreamina_path, "frames2video",
                   "--first", str(first_frame),
                   "--last", str(last_frame),
                   "--prompt", video_prompt,
                   "--poll", "180"]
        else:
            cmd = [dreamina_path, "image2video",
                   "--image", str(first_frame),
                   "--prompt", video_prompt,
                   "--duration", str(int(shot.get("duration", "3s").replace("s", ""))),
                   "--poll", "180"]

        ok = _run_dreamina_and_download(cmd, out, f"镜头{shot_num:03d}")
        results.append({"shot": shot_num, "file": str(out) if ok else None, "success": ok})

    _print_report(results, ws.videos_dir)
    return True


def _generate_videos_legacy(data, ws, args):
    """回退方案：使用 generate_video.py (即梦API / 海螺AI)"""
    print_header("AI 生视频 (即梦API - 回退方案)")
    engine = getattr(args, "video_engine", "jimeng")
    return run_script("generate_video.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--image-dir", str(ws.images_dir),
        "--output", str(ws.videos_dir),
        "--engine", engine,
        "--workspace", str(ws.root),
    ])


# ── 后期制作 ────────────────────────────────────────────────

def cmd_tts(args):
    """Phase 8a: TTS 配音"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()
    data = load_storyboard(args)
    if not data:
        return False
    print_header("TTS 台词配音")
    engine = getattr(args, "tts_engine", "free")
    return run_script("generate_tts.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--output", str(ws.tts_dir),
        "--engine", engine,
        "--workspace", str(ws.root),
    ])


def cmd_sfx(args):
    """Phase 8b: 音效生成"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()
    data = load_storyboard(args)
    if not data:
        return False
    print_header("音效生成")
    return run_script("generate_sfx.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--output", str(ws.sfx_dir),
        "--workspace", str(ws.root),
    ])


def cmd_subtitle(args):
    """Phase 8c: 字幕生成"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()
    data = load_storyboard(args)
    if not data:
        return False
    print_header("字幕生成")
    return run_script("generate_subtitle.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--tts-dir", str(ws.tts_dir),
        "--output", str(ws.subtitle_file),
        "--workspace", str(ws.root),
    ])


def cmd_assemble(args):
    """Phase 8d: 视频合成"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()
    data = load_storyboard(args)
    if not data:
        return False
    print_header("视频合成")
    return run_script("assemble_video.py", [
        "--data", json.dumps(data, ensure_ascii=False),
        "--video-dir", str(ws.videos_dir),
        "--tts-dir", str(ws.tts_dir),
        "--sfx-dir", str(ws.sfx_dir),
        "--bgm", str(ws.bgm_file),
        "--subtitle", str(ws.subtitle_file),
        "--output", str(ws.root / "最终成片.mp4"),
        "--workspace", str(ws.root),
    ])


# ── 全流程 ──────────────────────────────────────────────────

def cmd_all(args):
    """全流程一键执行"""
    ws = resolve_workspace(args)
    ws.ensure_dirs()

    print_header("AI 漫剧全流程开始")

    # Step 0: 检查环境
    print("\n[Step 0/6] 环境检查...")
    if not doctor(args):
        print("\n⚠  核心依赖缺失，请先安装缺失组件")
        proceed = input("  仍要继续？(y/N): ").strip().lower()
        if proceed != "y":
            print("  已取消")
            return False

    # Step 1: 生图
    print("\n[Step 1/6] AI 生图...")
    if not cmd_generate_images(args):
        print("  生图阶段出现问题，请检查后重试")
        return False

    # Step 2: 生视频
    print("\n[Step 2/6] AI 生视频...")
    if not cmd_generate_videos(args):
        print("  生视频阶段出现问题，请检查后重试")
        return False

    # Step 3: TTS + 音效
    print("\n[Step 3/6] TTS 配音...")
    cmd_tts(args)

    print("\n[Step 4/6] 音效生成...")
    cmd_sfx(args)

    # Step 4: 字幕
    print("\n[Step 5/6] 字幕生成...")
    cmd_subtitle(args)

    # Step 5: 合成
    print("\n[Step 6/6] 视频合成...")
    if not cmd_assemble(args):
        print("  合成失败")
        return False

    # 完成
    print_header("全流程完成")
    print(f"  项目: {ws.root}")
    print(f"  成片: {ws.root / '最终成片.mp4'}")
    print(f"  图片: {ws.images_dir}/")
    print(f"  视频: {ws.videos_dir}/")
    print(f"  音频: {ws.root / 'output_audio'}/")

    return True


# ── 通用辅助函数 ────────────────────────────────────────────

def load_storyboard(args):
    """从 --storyboard 或 --data 加载分镜JSON"""
    if hasattr(args, "storyboard") and args.storyboard:
        path = Path(args.storyboard)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        print(f"错误: 找不到分镜文件 {args.storyboard}")
        return None
    if hasattr(args, "data") and args.data:
        return json.loads(args.data)

    # 自动在工作区查找
    ws = resolve_workspace(args)
    sb = ws.storyboard_json()
    if sb.exists():
        with open(sb, "r", encoding="utf-8") as f:
            return json.load(f)

    print("错误: 请提供 --storyboard 参数或在工作区放置 分镜数据.json")
    return None


def _find_image(image_dir, prefix):
    """在图片目录中查找匹配前缀的图片"""
    image_dir = Path(image_dir)
    if not image_dir.exists():
        return None
    for ext in [".png", ".jpg", ".jpeg"]:
        p = image_dir / f"{prefix}{ext}"
        if p.exists():
            return p
    return None


def _print_report(results, output_dir):
    """打印生成报告"""
    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    print(f"\n  完成: {success}/{total}")
    print(f"  输出: {output_dir}")

    report_file = output_dir / "生成报告.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  报告: {report_file}")


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧全流程编排工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  doctor            检查所有前置依赖
  generate-images   批量生成图片（三视图 + 首帧/尾帧/代表画面）
  generate-videos   批量图生视频
  tts               TTS 台词配音
  sfx               音效生成
  subtitle          字幕生成
  assemble          视频合成（含 Doctor 检查）

  all               全流程一键执行（生图 → 生视频 → TTS → 音效 → 字幕 → 合成）

示例:
  # 检查环境
  python3 pipeline.py doctor --workspace /Volumes/dz/ai_video/我的项目/

  # 全流程
  python3 pipeline.py all --workspace /Volumes/dz/ai_video/我的项目/ --storyboard 分镜数据.json

  # 仅生图（使用dreamina CLI）
  python3 pipeline.py generate-images --workspace /Volumes/dz/ai_video/我的项目/ --storyboard 分镜数据.json

  # 仅生视频（使用生成的首帧+尾帧）
  python3 pipeline.py generate-videos --workspace /Volumes/dz/ai_video/我的项目/ --storyboard 分镜数据.json
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    add_workspace_args(parser)
    parser.add_argument("--storyboard", "-f", help="分镜 JSON 文件路径")
    parser.add_argument("--data", help="分镜 JSON 字符串")
    parser.add_argument("--force", action="store_true", help="强制重新生成（覆盖已有文件）")
    parser.add_argument("--aspect", default="16:9",
                        choices=["16:9", "9:16", "1:1", "2.35:1"],
                        help="画幅比例（默认 16:9）")
    parser.add_argument("--tts-engine", choices=["free", "doubao"], default="free",
                        help="TTS引擎（默认 free=edge-tts）")
    parser.add_argument("--video-engine", choices=["jimeng", "hailuo"], default="jimeng",
                        help="视频生成引擎回退方案（默认 jimeng）")

    subcmds = {
        "doctor": doctor,
        "generate-images": cmd_generate_images,
        "generate-videos": cmd_generate_videos,
        "tts": cmd_tts,
        "sfx": cmd_sfx,
        "subtitle": cmd_subtitle,
        "assemble": cmd_assemble,
        "all": cmd_all,
    }

    if len(sys.argv) < 2 or sys.argv[1] not in subcmds:
        parser.print_help()
        print(f"\n子命令: {', '.join(subcmds.keys())}")
        sys.exit(1)

    cmd = sys.argv[1]
    # 移除子命令名，让 parser 解析剩余参数
    sys.argv = [sys.argv[0]] + sys.argv[2:]

    args = parser.parse_args()

    success = subcmds[cmd](args)
    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
