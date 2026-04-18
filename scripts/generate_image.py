#!/usr/bin/env python3
"""
AI漫剧文生图脚本
使用火山引擎即梦AI生成漫剧图片：角色三视图、分镜首帧/尾帧/代表画面

API文档: https://www.volcengine.com/docs/85621/1817045

功能:
1. 单张生成：--prompt 指定提示词
2. 批量生成：--file 读取分镜JSON，自动生成所有图片
3. 三视图生成：--type turnaround 指定三视图模式
4. 支持多种画幅比例

配置方式（按优先级排序）:
1. 环境变量 VOLCENGINE_AK / VOLCENGINE_SK（推荐）
2. 配置文件 ~/.ai-comic-drama/config.json
3. 命令行参数 --ak --sk
"""

import argparse
import json
import os
import sys
import time
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace


# ── 配置管理 ──────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".ai-comic-drama"
CONFIG_FILE = CONFIG_DIR / "config.json"

# 画幅预设（宽×高）
ASPECT_PRESETS = {
    "9:16": (1080, 1920),    # 竖屏（抖音/快手）
    "16:9": (1920, 1080),    # 横屏（B站/YouTube）
    "1:1": (1024, 1024),     # 正方形
    "3:4": (864, 1152),      # 竖版海报
    "4:3": (1152, 864),      # 横版海报
    "2.35:1": (2322, 988),   # 电影宽屏
}

# 三视图固定尺寸（正方形，纯白背景）
TURNAROUND_SIZE = (1024, 1024)


def get_credentials():
    """按优先级获取 AK/SK：环境变量 > 配置文件"""
    # 1. 环境变量
    ak = os.environ.get("VOLCENGINE_AK")
    sk = os.environ.get("VOLCENGINE_SK")
    if ak and sk:
        return ak, sk

    # 2. 配置文件
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config.get("ak"), config.get("sk")
        except Exception:
            pass

    return None, None


def save_credentials(ak, sk):
    """保存 AK/SK 到配置文件"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config = {"ak": ak, "sk": sk}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config.update(json.load(f))
        except Exception:
            pass
    config["ak"] = ak
    config["sk"] = sk
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"配置已保存到 {CONFIG_FILE}")


def setup_credentials():
    """交互式配置 AK/SK"""
    print("=" * 50)
    print("火山引擎即梦AI AK/SK 配置")
    print("=" * 50)
    print()
    print("获取 AK/SK: https://console.volcengine.com/iam/keymanage")
    print()

    ak = input("请输入 Access Key (AK): ").strip()
    if not ak:
        print("AK 不能为空")
        return False

    sk = input("请输入 Secret Key (SK): ").strip()
    if not sk:
        print("SK 不能为空")
        return False

    save_credentials(ak, sk)
    print("配置完成！后续使用时会自动读取。")
    return True


# ── 即梦 AI 客户端 ────────────────────────────────────────

class JimengImageClient:
    """即梦AI 文生图客户端（异步模式）"""

    def __init__(self, ak, sk):
        from volcengine.visual.VisualService import VisualService

        self.client = VisualService()
        self.client.set_ak(ak)
        self.client.set_sk(sk)
        self.req_key = "jimeng_t2i_v40"

    def submit(self, prompt, width=None, height=None, scale=0.5):
        """提交文生图异步任务"""
        form = {
            "req_key": self.req_key,
            "prompt": prompt,
            "scale": scale,
            "force_single": True,
        }
        if width and height:
            form["width"] = width
            form["height"] = height

        try:
            resp = self.client.cv_sync2async_submit_task(form)
            if resp.get("code") == 10000 and "data" in resp:
                task_id = resp["data"].get("task_id")
                return task_id
            else:
                print(f"  提交失败: {resp.get('message', 'Unknown')}")
                return None
        except Exception as e:
            print(f"  提交异常: {e}")
            return None

    def poll_result(self, task_id, interval=3, max_wait=180):
        """轮询任务结果"""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                form = {"req_key": self.req_key, "task_id": task_id}
                resp = self.client.cv_sync2async_get_result(form)

                if resp.get("code") == 10000 and "data" in resp:
                    status = resp["data"].get("status")
                    if status == "done":
                        return resp
                    elif status in ("in_queue", "generating"):
                        elapsed = int(time.time() - start)
                        print(f"  处理中... ({elapsed}s)", end="\r")
                        time.sleep(interval)
                    else:
                        print(f"  状态异常: {status}")
                        return None
                else:
                    print(f"  查询失败: {resp.get('message', 'Unknown')}")
                    return None
            except Exception as e:
                print(f"  查询异常: {e}")
                return None

        print(f"  超时（{max_wait}s）")
        return None

    def generate(self, prompt, output_path, width=None, height=None,
                 scale=0.5, max_wait=180):
        """生成单张图片：提交 → 轮询 → 保存"""
        print(f"  生成: {prompt[:50]}...")
        task_id = self.submit(prompt, width, height, scale)
        if not task_id:
            return False

        resp = self.poll_result(task_id, max_wait=max_wait)
        if not resp:
            return False

        return self._save(resp, output_path)

    def _save(self, resp, output_path):
        """从响应中保存图片"""
        try:
            data = resp["data"]

            # 优先使用 URL
            if "image_urls" in data and data["image_urls"]:
                import requests
                url = data["image_urls"][0]
                r = requests.get(url, timeout=30)
                if r.status_code == 200:
                    img_data = r.content
                else:
                    raise Exception("下载失败")
            elif "binary_data_base64" in data and data["binary_data_base64"]:
                img_data = base64.b64decode(data["binary_data_base64"][0])
            else:
                print("  响应中无图片数据")
                return False

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(img_data)

            print(f"  已保存: {output_path} ({len(img_data)} bytes)")
            return True
        except Exception as e:
            print(f"  保存失败: {e}")
            return False


# ── 批量生成 ──────────────────────────────────────────────

def batch_generate(client, data, output_dir, aspect_ratio="16:9"):
    """从分镜 JSON 批量生成图片"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = data.get("metadata", {})
    title = metadata.get("title", "未命名")
    characters = data.get("characters", [])
    shots = data.get("shots", [])

    # 解析画幅尺寸
    ar = ASPECT_PRESETS.get(aspect_ratio, (1920, 1080))

    results = {"characters": [], "shots": []}

    # ── 1. 生成角色三视图 ──
    if characters:
        print(f"\n{'='*50}")
        print(f"生成角色三视图 ({len(characters)} 个角色)")
        print(f"{'='*50}")

        for i, char in enumerate(characters):
            name = char.get("name", f"角色{i+1}")
            prompt = char.get("turnaround_prompt", "")
            if not prompt:
                print(f"  跳过 {name}：无三视图提示词")
                continue

            out_file = output_dir / f"三视图_{name}.png"
            print(f"\n[{i+1}/{len(characters)}] {name}")

            success = client.generate(
                prompt=prompt,
                output_path=out_file,
                width=TURNAROUND_SIZE[0],
                height=TURNAROUND_SIZE[1],
            )
            results["characters"].append({
                "name": name,
                "file": str(out_file) if success else None,
                "success": success,
            })

    # ── 2. 生成分镜图片 ──
    if shots:
        print(f"\n{'='*50}")
        print(f"生成分镜图片 ({len(shots)} 个镜头)")
        print(f"{'='*50}")

        for i, shot in enumerate(shots):
            shot_num = shot.get("shot_number", i + 1)
            scene = shot.get("scene", "")

            # 生成首帧、尾帧、代表画面
            for img_type, key in [
                ("首帧", "first_frame_prompt"),
                ("尾帧", "last_frame_prompt"),
                ("代表画面", "image_prompt"),
            ]:
                prompt = shot.get(key, "")
                if not prompt:
                    continue

                out_file = output_dir / f"镜头{shot_num:03d}_{img_type}.png"
                print(f"\n[{i+1}/{len(shots)}] 镜头{shot_num} - {img_type}")

                success = client.generate(
                    prompt=prompt,
                    output_path=out_file,
                    width=ar[0],
                    height=ar[1],
                )
                results["shots"].append({
                    "shot": shot_num,
                    "type": img_type,
                    "file": str(out_file) if success else None,
                    "success": success,
                })

    # ── 输出报告 ──
    print(f"\n{'='*50}")
    print(f"生成完成：{title}")
    print(f"{'='*50}")

    total = len(results["characters"]) + len(results["shots"])
    success_count = sum(
        1 for r in results["characters"] + results["shots"] if r["success"]
    )
    print(f"总计: {success_count}/{total} 成功")
    print(f"输出目录: {output_dir}")

    # 保存结果报告
    report_file = output_dir / "生成报告.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"报告: {report_file}")

    return results


# ── 主入口 ────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="AI漫剧文生图工具（火山引擎即梦AI）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式配置
  python generate_image.py --setup

  # 单张生成
  python generate_image.py --prompt "国漫3d风格，古风场景" --output scene.png

  # 指定画幅
  python generate_image.py --prompt "..." --output out.png --aspect 9:16

  # 从分镜JSON批量生成
  python generate_image.py --file 分镜表.json --output ./images --aspect 16:9

获取AK/SK: https://console.volcengine.com/iam/keymanage
        """,
    )

    # 操作模式
    parser.add_argument("--setup", action="store_true", help="交互式配置 AK/SK")

    # 生成参数
    parser.add_argument("--prompt", help="文生图提示词")
    parser.add_argument("--output", "-o", help="输出文件路径（单张）或目录（批量）")
    parser.add_argument("--file", "-f", help="分镜 JSON 文件路径（批量模式）")
    parser.add_argument("--data", help="分镜 JSON 字符串（批量模式）")

    # 画幅参数
    parser.add_argument(
        "--aspect",
        choices=list(ASPECT_PRESETS.keys()),
        default="16:9",
        help="画幅比例（默认 16:9）",
    )
    parser.add_argument("--width", type=int, help="自定义宽度")
    parser.add_argument("--height", type=int, help="自定义高度")

    # 生成参数
    parser.add_argument("--scale", type=float, default=0.5, help="文本权重 (0-1, 默认0.5)")
    parser.add_argument("--timeout", type=int, default=180, help="单张超时秒数（默认180）")

    # 认证参数
    parser.add_argument("--ak", help="火山引擎 Access Key")
    parser.add_argument("--sk", help="火山引擎 Secret Key")

    add_workspace_args(parser)

    args = parser.parse_args()
    ws = resolve_workspace(args)

    # ── setup 模式 ──
    if args.setup:
        setup_credentials()
        return

    # ── 获取凭证 ──
    ak, sk = get_credentials()
    if not ak or not sk:
        ak, sk = args.ak, args.sk

    if not ak or not sk:
        print("未找到 AK/SK，请先配置：")
        print("  python generate_image.py --setup")
        print("  或设置环境变量 VOLCENGINE_AK / VOLCENGINE_SK")
        sys.exit(1)

    # 命令行传入时保存
    if args.ak and args.sk:
        save_credentials(args.ak, args.sk)
        os.environ["VOLCENGINE_AK"] = args.ak
        os.environ["VOLCENGINE_SK"] = args.sk

    # ── 创建客户端 ──
    client = JimengImageClient(ak, sk)

    # ── 批量模式 ──
    if args.file or args.data:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(args.data)

        output_dir = args.output or str(ws.images_dir)
        batch_generate(client, data, output_dir, args.aspect)
        return

    # ── 单张模式 ──
    if not args.prompt or not args.output:
        parser.print_help()
        print("\n错误: 单张模式需要 --prompt 和 --output")
        sys.exit(1)

    width = args.width
    height = args.height
    if not width or not height:
        width, height = ASPECT_PRESETS.get(args.aspect, (1920, 1080))

    success = client.generate(
        prompt=args.prompt,
        output_path=args.output,
        width=width,
        height=height,
        scale=args.scale,
        max_wait=args.timeout,
    )

    if success:
        print(f"\n完成: {args.output}")
    else:
        print(f"\n生成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
