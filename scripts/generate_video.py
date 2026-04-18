#!/usr/bin/env python3
"""
AI漫剧图生视频脚本
使用火山引擎即梦AI 1080P模型将分镜图片生成视频（V4签名HTTP API）

API文档: https://www.volcengine.com/docs/85621/1802721

模型：即梦AI-视频生成3.0 1080P
- 首帧+尾帧：req_key = jimeng_i2v_first_tail_v30_1080
- 仅首帧：   req_key = jimeng_i2v_first_v30_1080

功能:
1. 单个生成：--image 指定输入图片，--prompt 指定视频提示词
2. 批量生成：--file 读取分镜JSON + 图片目录，自动生成所有视频
3. 支持首帧/尾帧输入，生成过渡视频
4. 并发生成：--concurrency 指定并发数（默认2）

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
import threading
import datetime
import hashlib
import hmac
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

sys.path.insert(0, str(Path(__file__).parent))
from workspace import add_workspace_args, resolve_workspace

# ── 配置管理 ──────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".ai-comic-drama"
CONFIG_FILE = CONFIG_DIR / "config.json"


def get_credentials():
    """按优先级获取 AK/SK"""
    ak = os.environ.get("VOLCENGINE_AK")
    sk = os.environ.get("VOLCENGINE_SK")
    if ak and sk:
        return ak, sk

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
    config = {}
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
        except Exception:
            pass
    config["ak"] = ak
    config["sk"] = sk
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


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
    print("配置完成！")
    return True


def encode_image_base64(image_path):
    """将图片文件编码为 base64"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ── V4 签名（火山引擎 HTTP API） ──────────────────────────

API_HOST = "visual.volcengineapi.com"
API_REGION = "cn-north-1"
API_ENDPOINT = "https://visual.volcengineapi.com"
API_SERVICE = "cv"


def _sign(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signature_key(key, date_stamp, region_name, service_name):
    k_date = _sign(key.encode("utf-8"), date_stamp)
    k_region = _sign(k_date, region_name)
    k_service = _sign(k_region, service_name)
    k_signing = _sign(k_service, "request")
    return k_signing


def _format_query(parameters):
    parts = []
    for key in sorted(parameters):
        parts.append(f"{key}={parameters[key]}")
    return "&".join(parts)


def _build_signed_headers(ak, sk, query_str, body_str):
    """构建 V4 签名请求头"""
    method = "POST"
    t = datetime.datetime.utcnow()
    current_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
    content_type = "application/json"
    signed_headers = "content-type;host;x-content-sha256;x-date"

    canonical_headers = (
        f"content-type:{content_type}\n"
        f"host:{API_HOST}\n"
        f"x-content-sha256:{payload_hash}\n"
        f"x-date:{current_date}\n"
    )

    canonical_request = (
        f"{method}\n/\n{query_str}\n{canonical_headers}\n"
        f"{signed_headers}\n{payload_hash}"
    )

    credential_scope = f"{date_stamp}/{API_REGION}/{API_SERVICE}/request"
    string_to_sign = (
        f"HMAC-SHA256\n{current_date}\n{credential_scope}\n"
        f"{hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()}"
    )

    signing_key = _get_signature_key(sk, date_stamp, API_REGION, API_SERVICE)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    authorization = (
        f"HMAC-SHA256 Credential={ak}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    return {
        "X-Date": current_date,
        "Authorization": authorization,
        "X-Content-Sha256": payload_hash,
        "Content-Type": content_type,
    }


def _api_call(ak, sk, action, body_dict):
    """发起一次 V4 签名 API 调用"""
    query_params = {
        "Action": action,
        "Version": "2022-08-31",
    }
    query_str = _format_query(query_params)
    body_str = json.dumps(body_dict)
    headers = _build_signed_headers(ak, sk, query_str, body_str)
    url = f"{API_ENDPOINT}?{query_str}"

    r = requests.post(url, headers=headers, data=body_str.encode("utf-8"),
                      timeout=120)
    return r.json()


# ── 即梦 AI 图生视频客户端 ────────────────────────────────

_print_lock = threading.Lock()


def safe_print(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)


# 时长 → 帧数映射（1080P模型仅支持 121=5s / 241=10s）
def duration_to_frames(seconds):
    if seconds <= 5:
        return 121
    else:
        return 241


# req_key 选择
REQ_KEY_FIRST_ONLY = "jimeng_i2v_first_v30_1080"
REQ_KEY_FIRST_TAIL = "jimeng_i2v_first_tail_v30_1080"


class JimengVideoClient:
    """即梦AI 图生视频客户端（V4签名HTTP，线程安全）"""

    def __init__(self, ak, sk):
        self.ak = ak
        self.sk = sk
        self._submit_lock = threading.Lock()

    def _choose_req_key(self, has_last_frame):
        return REQ_KEY_FIRST_TAIL if has_last_frame else REQ_KEY_FIRST_ONLY

    def submit(self, prompt, image_base64=None, last_frame_base64=None,
               duration=5, shot_label=""):
        """
        提交图生视频异步任务

        有首帧+尾帧 → jimeng_i2v_first_tail_v30_1080
        仅首帧     → jimeng_i2v_first_v30_1080

        binary_data_base64:
          - 仅首帧: [first_b64]
          - 首帧+尾帧: [first_b64, last_b64]
        """
        if not image_base64:
            safe_print(f"  [{shot_label}] 错误：至少需要首帧图片")
            return None

        has_last = bool(last_frame_base64)
        req_key = self._choose_req_key(has_last)

        binary_data = [image_base64]
        if has_last:
            binary_data.append(last_frame_base64)

        form = {
            "req_key": req_key,
            "binary_data_base64": binary_data,
            "prompt": prompt,
            "frames": duration_to_frames(duration),
        }

        max_retries = 8
        for attempt in range(max_retries):
            try:
                with self._submit_lock:
                    resp = _api_call(self.ak, self.sk,
                                     "CVSync2AsyncSubmitTask", form)

                code = resp.get("code")
                if code == 10000 and "data" in resp:
                    task_id = resp["data"].get("task_id")
                    return task_id
                elif code == 50430:
                    wait = 10 * (attempt + 1)
                    safe_print(f"  [{shot_label}] 并发限制，{wait}s 后重试 "
                               f"({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                else:
                    safe_print(f"  [{shot_label}] 提交失败: code={code}, "
                               f"{resp.get('message', 'Unknown')}")
                    return None
            except Exception as e:
                error_str = str(e)
                if "50430" in error_str or "Concurrent" in error_str:
                    wait = 10 * (attempt + 1)
                    safe_print(f"  [{shot_label}] 并发限制，{wait}s 后重试 "
                               f"({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                safe_print(f"  [{shot_label}] 提交异常: {e}")
                return None

        safe_print(f"  [{shot_label}] 提交失败：超过最大重试次数")
        return None

    def poll_result(self, task_id, req_key, interval=5, max_wait=600,
                    shot_label=""):
        """轮询任务结果"""
        start = time.time()
        while time.time() - start < max_wait:
            try:
                form = {"req_key": req_key, "task_id": task_id}
                resp = _api_call(self.ak, self.sk,
                                 "CVSync2AsyncGetResult", form)

                code = resp.get("code")
                if code == 10000 and "data" in resp:
                    status = resp["data"].get("status")
                    if status == "done":
                        return resp
                    elif status in ("in_queue", "generating"):
                        elapsed = int(time.time() - start)
                        safe_print(f"  [{shot_label}] {status} ({elapsed}s)")
                        time.sleep(interval)
                    else:
                        safe_print(f"  [{shot_label}] 状态异常: {status}")
                        return None
                else:
                    safe_print(f"  [{shot_label}] 查询失败: code={code}, "
                               f"{resp.get('message', 'Unknown')}")
                    return None
            except Exception as e:
                safe_print(f"  [{shot_label}] 查询异常: {e}")
                return None

        safe_print(f"  [{shot_label}] 超时（{max_wait}s）")
        return None

    def generate(self, prompt, output_path, image_path=None,
                 last_frame_path=None, duration=5, max_wait=600,
                 shot_label=""):
        """生成视频：提交 → 轮询 → 保存"""
        has_tail = last_frame_path and Path(last_frame_path).exists()
        mode = "首帧+尾帧" if has_tail else "仅首帧"
        safe_print(f"  [{shot_label}] 模式: {mode}, "
                   f"提示词: {prompt[:50]}...")

        # 编码首帧图片
        img_b64 = None
        if image_path and Path(image_path).exists():
            img_b64 = encode_image_base64(image_path)

        # 编码尾帧图片
        last_b64 = None
        if last_frame_path and Path(last_frame_path).exists():
            last_b64 = encode_image_base64(last_frame_path)

        # 提交任务
        task_id = self.submit(
            prompt=prompt,
            image_base64=img_b64,
            last_frame_base64=last_b64,
            duration=duration,
            shot_label=shot_label,
        )
        if not task_id:
            return False

        # 确定 req_key 用于轮询
        req_key = self._choose_req_key(has_tail)

        # 轮询结果
        resp = self.poll_result(task_id, req_key, max_wait=max_wait,
                                shot_label=shot_label)
        if not resp:
            return False

        return self._save(resp, output_path, shot_label)

    def _save(self, resp, output_path, shot_label=""):
        """从响应中保存视频"""
        try:
            data = resp["data"]

            # 优先使用 URL
            url = None
            if "video_url" in data and data["video_url"]:
                url = data["video_url"]
            elif "video_urls" in data and data["video_urls"]:
                url = data["video_urls"][0]

            if url:
                safe_print(f"  [{shot_label}] 下载视频...")
                r = requests.get(url, timeout=120)
                if r.status_code == 200:
                    video_data = r.content
                else:
                    raise Exception(f"下载失败: HTTP {r.status_code}")
            elif "binary_data_base64" in data and data["binary_data_base64"]:
                video_data = base64.b64decode(data["binary_data_base64"][0])
            else:
                safe_print(f"  [{shot_label}] 响应中无视频数据")
                return False

            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(video_data)

            size_mb = len(video_data) / 1024 / 1024
            safe_print(f"  [{shot_label}] 已保存: {output_path.name} "
                       f"({size_mb:.1f} MB)")
            return True
        except Exception as e:
            safe_print(f"  [{shot_label}] 保存失败: {e}")
            return False


# ── 批量生成（并发版） ──────────────────────────────────────

def batch_generate(client, data, image_dir, output_dir, concurrency=2):
    """从分镜 JSON + 已生成的图片 并发批量生成视频"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    image_dir = Path(image_dir)
    shots = data.get("shots", [])
    metadata = data.get("metadata", {})

    # 准备任务列表
    tasks = []
    for i, shot in enumerate(shots):
        shot_num = shot.get("shot_number", i + 1)
        video_prompt = shot.get("video_prompt", "")
        duration_str = shot.get("duration", "3s")

        try:
            duration = float(duration_str.replace("s", "").strip())
        except (ValueError, AttributeError):
            duration = 3

        if not video_prompt:
            continue

        # 查找首帧图片
        first_frame = None
        for pattern in [
            f"镜头{shot_num:03d}_首帧.png",
            f"镜头{shot_num:03d}_首帧.jpg",
            f"镜头{shot_num:03d}_代表画面.png",
            f"镜头{shot_num:03d}_代表画面.jpg",
        ]:
            p = image_dir / pattern
            if p.exists():
                first_frame = str(p)
                break

        # 查找尾帧图片
        last_frame = None
        for pattern in [
            f"镜头{shot_num:03d}_尾帧.png",
            f"镜头{shot_num:03d}_尾帧.jpg",
        ]:
            p = image_dir / pattern
            if p.exists():
                last_frame = str(p)
                break

        out_file = output_dir / f"镜头{shot_num:03d}.mp4"

        # 跳过已存在的文件
        if out_file.exists():
            safe_print(f"  [镜头{shot_num:03d}] 已存在，跳过")
            tasks.append({
                "shot_num": shot_num,
                "out_file": str(out_file),
                "skipped": True,
            })
            continue

        tasks.append({
            "shot_num": shot_num,
            "prompt": video_prompt,
            "duration": int(duration),
            "first_frame": first_frame,
            "last_frame": last_frame,
            "out_file": str(out_file),
            "skipped": False,
        })

    total = len(tasks)
    need_gen = sum(1 for t in tasks if not t["skipped"])

    print(f"\n{'='*50}")
    print(f"批量生成视频 (共{total}个，需生成{need_gen}个，并发{concurrency})")
    print(f"模型: 即梦AI-视频生成3.0 1080P")
    print(f"{'='*50}")

    results = []

    # 并发执行
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_task = {}
        for i, task in enumerate(tasks):
            if task["skipped"]:
                results.append({
                    "shot": task["shot_num"],
                    "file": task["out_file"],
                    "success": True,
                    "skipped": True,
                })
                continue

            shot_label = f"{i+1}/{total} 镜头{task['shot_num']:03d}"
            safe_print(f"\n[{shot_label}] 提交...")
            future = executor.submit(
                client.generate,
                prompt=task["prompt"],
                output_path=task["out_file"],
                image_path=task["first_frame"],
                last_frame_path=task["last_frame"],
                duration=task["duration"],
                max_wait=600,
                shot_label=shot_label,
            )
            future_to_task[future] = task

        # 等待所有任务完成
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                success = future.result()
            except Exception as e:
                safe_print(f"  [镜头{task['shot_num']:03d}] 线程异常: {e}")
                success = False

            results.append({
                "shot": task["shot_num"],
                "file": task["out_file"] if success else None,
                "success": success,
                "prompt": task.get("prompt", ""),
                "first_frame": task.get("first_frame"),
                "last_frame": task.get("last_frame"),
            })

    # 输出报告
    results.sort(key=lambda r: r["shot"])

    print(f"\n{'='*50}")
    print(f"视频生成完成：{metadata.get('title', '未命名')}")
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
        description="AI漫剧图生视频工具（火山引擎即梦AI 1080P）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式配置
  python generate_video.py --setup

  # 单个生成（图片+提示词 → 视频）
  python generate_video.py --image scene.png --prompt "角色转身，镜头缓慢拉远" --output video.mp4

  # 首帧+尾帧生成
  python generate_video.py --image first.png --last-frame last.png --prompt "角色从站立到坐下" --output video.mp4

  # 从分镜JSON批量生成（默认2并发）
  python generate_video.py --file 分镜表.json --image-dir ./images --output ./videos

  # 指定并发数
  python generate_video.py --file 分镜表.json --image-dir ./images --output ./videos --concurrency 2

获取AK/SK: https://console.volcengine.com/iam/keymanage
        """,
    )

    # 操作模式
    parser.add_argument("--setup", action="store_true", help="交互式配置 AK/SK")

    # 生成参数
    parser.add_argument("--prompt", help="视频动作提示词")
    parser.add_argument("--image", help="首帧图片路径")
    parser.add_argument("--last-frame", help="尾帧图片路径")
    parser.add_argument("--output", "-o", help="输出视频路径（单个）或目录（批量）")
    parser.add_argument("--duration", type=int, default=5, help="视频时长秒数（默认5）")

    # 批量参数
    parser.add_argument("--file", "-f", help="分镜 JSON 文件路径（批量模式）")
    parser.add_argument("--data", help="分镜 JSON 字符串（批量模式）")
    parser.add_argument("--image-dir", help="图片目录（批量模式）")
    parser.add_argument("--concurrency", "-j", type=int, default=2,
                        help="并发数（默认2）")

    # 超时
    parser.add_argument("--timeout", type=int, default=600,
                        help="单个视频最大等待秒数（默认600）")

    # 认证参数
    parser.add_argument("--ak", help="火山引擎 Access Key")
    parser.add_argument("--sk", help="火山引擎 Secret Key")

    add_workspace_args(parser)

    args = parser.parse_args()
    ws = resolve_workspace(args)

    # setup 模式
    if args.setup:
        setup_credentials()
        return

    # 获取凭证
    ak, sk = get_credentials()
    if not ak or not sk:
        ak, sk = args.ak, args.sk

    if not ak or not sk:
        print("未找到 AK/SK，请先配置：")
        print("  python generate_video.py --setup")
        print("  或设置环境变量 VOLCENGINE_AK / VOLCENGINE_SK")
        sys.exit(1)

    if args.ak and args.sk:
        save_credentials(args.ak, args.sk)
        os.environ["VOLCENGINE_AK"] = args.ak
        os.environ["VOLCENGINE_SK"] = args.sk

    # 创建客户端
    client = JimengVideoClient(ak, sk)

    # 批量模式
    if args.file or args.data:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = json.loads(args.data)

        image_dir = args.image_dir or str(ws.images_dir)
        output_dir = args.output or str(ws.videos_dir)
        batch_generate(client, data, image_dir, output_dir, args.concurrency)
        return

    # 单个模式
    if not args.prompt or not args.output:
        parser.print_help()
        print("\n错误: 需要 --prompt 和 --output")
        sys.exit(1)

    success = client.generate(
        prompt=args.prompt,
        output_path=args.output,
        image_path=args.image,
        last_frame_path=args.last_frame,
        duration=args.duration,
        max_wait=args.timeout,
    )

    if success:
        print(f"\n完成: {args.output}")
    else:
        print(f"\n生成失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
