"""
工作区路径管理工具
所有漫剧脚本共用的工作区（workspace）路径解析逻辑。

用法:
  from workspace import resolve_workspace, add_workspace_args

  # 在 argparse 中:
  add_workspace_args(parser)

  # 解析后:
  ws = resolve_workspace(args)
  tts_dir = ws.tts_dir
  video_dir = ws.video_dir
"""

import json
from pathlib import Path


class Workspace:
    """项目工作区路径管理"""

    def __init__(self, path=None):
        if path:
            self.root = Path(path)
        else:
            self.root = Path.cwd()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def images_dir(self):
        return self.root / "output_images"

    @property
    def videos_dir(self):
        return self.root / "output_videos"

    @property
    def tts_dir(self):
        return self.root / "output_audio" / "tts"

    @property
    def sfx_dir(self):
        return self.root / "output_audio" / "sfx"

    @property
    def bgm_file(self):
        return self.root / "output_audio" / "bgm.mp3"

    @property
    def subtitle_file(self):
        return self.root / "output_audio" / "subtitle.srt"

    @property
    def report_file(self):
        return self.root / "生成报告.json"

    def storyboard_json(self, title=""):
        """分镜数据 JSON 路径"""
        if title:
            return self.root / f"{title}_分镜数据.json"
        # 自动查找
        for f in self.root.glob("*分镜数据.json"):
            return f
        return self.root / "分镜数据.json"

    def ensure_dirs(self):
        """创建所有输出子目录"""
        for d in [self.images_dir, self.videos_dir, self.tts_dir, self.sfx_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def init(self, title=""):
        """初始化空白工作区"""
        self.ensure_dirs()
        print(f"工作区已初始化: {self.root}")
        print(f"  output_images/  图片输出")
        print(f"  output_videos/  视频输出")
        print(f"  output_audio/tts/  TTS配音")
        print(f"  output_audio/sfx/  音效")
        if title:
            print(f"  项目: {title}")


def add_workspace_args(parser):
    """给 argparse parser 添加 --workspace 参数"""
    parser.add_argument(
        "--workspace", "-w",
        help="项目工作区目录（所有输出归到此目录下）。"
             "例如: --workspace /Volumes/dz/ai_video/我的项目/")


def resolve_workspace(args):
    """从命令行参数解析工作区，返回 Workspace 对象"""
    ws_path = getattr(args, 'workspace', None)
    if ws_path:
        return Workspace(ws_path)
    return Workspace()
