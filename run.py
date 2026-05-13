"""
bilibili-receptionist 入口脚本
用法：
    python run.py              # 启动定时循环
    python run.py --once       # 执行一轮后退出
    python run.py --check      # 仅检测 Cookie 有效性
    python run.py --add BVxxx  # 添加视频到监控列表
    python run.py --list       # 列出监控视频
"""

import sys
import argparse
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config as cfg
import bilibili_api as api
from receptionist import (
    setup_logging,
    run_once,
    run_loop,
    MonitoredVideos,
    RepliedTracker,
)


def cmd_run():
    """启动定时循环"""
    setup_logging()
    # 配置校验
    issues = cfg.validate_config()
    if issues:
        print("❌ 配置校验失败：")
        for issue in issues:
            print(f"  - {issue}")
        print("\n请参照 config.example.json 修复 config.json")
        sys.exit(1)

    run_loop()


def cmd_once(mode: str = "all"):
    """执行一轮"""
    setup_logging()
    issues = cfg.validate_config()
    if issues:
        print("❌ 配置校验失败：")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    result = run_once(mode=mode)
    print(f"\n✅ 完成: 处理 {result['videos_processed']} 个视频, 发送 {result['total_replies']} 条回复")
    for d in result["details"]:
        print(f"  {d['bvid']}: {d['replies']} 条回复")


def cmd_check():
    """检测 Cookie"""
    issues = cfg.validate_config()
    if issues:
        print("❌ 配置校验失败：")
        for issue in issues:
            print(f"  - {issue}")
        sys.exit(1)

    print("检测 Cookie 有效性...")
    if api.check_cookie_valid():
        print("✅ Cookie 有效")
    else:
        print("❌ Cookie 已过期，请更新 config.json")
        sys.exit(1)


def cmd_add(bvid: str, priority: int = 0):
    """添加视频到监控列表"""
    videos = MonitoredVideos()
    videos.add_video(bvid, priority=priority)
    print(f"✅ 已添加: {bvid} (priority={priority})")
    print(f"当前监控 {len(videos)} 个视频")


def cmd_import(json_path: str):
    """从 JSON 文件批量导入 BV 号"""
    import json as _json
    path = Path(json_path)
    if not path.exists():
        print(f"❌ 文件不存在: {json_path}")
        sys.exit(1)

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except _json.JSONDecodeError as e:
        print(f"❌ JSON 解析失败: {e}")
        sys.exit(1)

    # 支持多种格式
    bvids = []
    if isinstance(data, list):
        # 纯列表: ["BV1xxx", "BV2xxx"]
        bvids = [(item, 0) if isinstance(item, str) else (item.get("bvid", ""), item.get("priority", 0)) for item in data]
    elif isinstance(data, dict):
        # 对象格式: {"bvids": [...]} 或 {"videos": [...]}
        items = data.get("bvids") or data.get("videos") or []
        bvids = [(item, 0) if isinstance(item, str) else (item.get("bvid", ""), item.get("priority", 0)) for item in items]

    if not bvids:
        print("⚠️ 未找到 BV 号")
        return

    videos = MonitoredVideos()
    added = 0
    for bvid, priority in bvids:
        if not bvid or not bvid.startswith("BV"):
            print(f"  跳过无效 BV号: {bvid}")
            continue
        videos.add_video(bvid, priority=priority)
        added += 1

    print(f"✅ 批量导入完成: 新增/更新 {added} 个视频，当前监控 {len(videos)} 个")


def cmd_export():
    """导出监控列表为 JSON"""
    videos = MonitoredVideos()
    if not videos:
        print("📭 监控列表为空")
        return
    data = {
        "videos": videos.get_sorted_videos(),
        "exported_at": __import__("datetime").datetime.now().isoformat(),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_discover():
    """自动发现频道所有视频并加入监控列表"""
    uid = cfg.get_uid()
    print(f"🔍 正在拉取用户 {uid} 的所有视频...")
    try:
        videos_list = api.fetch_all_user_videos(int(uid))
    except Exception as e:
        print(f"❌ 拉取失败: {e}")
        sys.exit(1)

    if not videos_list:
        print("📭 未找到任何视频")
        return

    videos = MonitoredVideos()
    added = 0
    for v in videos_list:
        bvid = v["bvid"]
        title = v.get("title", "")
        pubdate = v.get("created", 0)
        videos.add_video(bvid, title=title, priority=0, pubdate=pubdate)
        added += 1

    print(f"✅ 已将 {added} 个视频加入监控列表:")
    for v in videos.get_sorted_videos():
        pub = v.get("pubdate", 0)
        from datetime import datetime as _dt
        pub_str = _dt.fromtimestamp(pub).strftime("%Y-%m-%d") if pub else "?"
        print(f"  {v['bvid']} [{pub_str}] {v.get('title', '(无标题)')}")
    print(f"\n当前共监控 {len(videos)} 个视频")


def cmd_list():
    """列出监控视频"""
    videos = MonitoredVideos()
    if not videos:
        print("📭 监控列表为空")
        return

    print(f"📋 监控视频 ({len(videos)} 个):")
    for v in videos.get_sorted_videos():
        priority_mark = {0: "", 1: " ⭐", 2: " ⭐⭐"}.get(v.get("priority", 0), "")
        title = v.get("title", "(无标题)")
        print(f"  {v['bvid']}{priority_mark} - {title}")


def main():
    parser = argparse.ArgumentParser(description="B站评论接待员 - 荒野小爪")
    parser.add_argument("--once", action="store_true", help="执行一轮后退出")
    parser.add_argument("--check", action="store_true", help="检测 Cookie 有效性")
    parser.add_argument("--add", type=str, metavar="BV号", help="添加视频到监控列表")
    parser.add_argument("--priority", type=int, default=0, help="视频优先级 (0=普通, 1=新上传, 2=高优先)")
    parser.add_argument("--list", action="store_true", help="列出监控视频")
    parser.add_argument("--import-bvids", type=str, metavar="FILE", help="从 JSON 文件批量导入 BV 号")
    parser.add_argument("--export", action="store_true", help="导出监控列表为 JSON")
    parser.add_argument("--discover", action="store_true", help="自动发现频道所有视频并加入监控")
    parser.add_argument("--mode", type=str, default="new", choices=["all", "new"],
                        help="检查模式: new=只检查近3天新视频(默认), all=所有视频")

    args = parser.parse_args()

    if args.check:
        cmd_check()
    elif args.add:
        cmd_add(args.add, args.priority)
    elif args.list:
        cmd_list()
    elif args.discover:
        cmd_discover()
    elif args.import_bvids:
        cmd_import(args.import_bvids)
    elif args.export:
        cmd_export()
    elif args.once:
        cmd_once(mode=args.mode)
    else:
        cmd_run()


if __name__ == "__main__":
    main()
