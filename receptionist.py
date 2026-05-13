"""
bilibili-receptionist 主逻辑模块
协调评论抓取 → 过滤 → 回复生成 → 发送 → 日志记录的完整流程。
"""

import json
import time
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config as cfg
import bilibili_api as api
from reply_generator import generate_unique_reply, is_sensitive, SAFE_REPLY

logger = logging.getLogger("receptionist")


# ============================================================
# 已回复记录持久化
# ============================================================

class RepliedTracker:
    """已回复评论 ID 追踪器，支持持久化"""

    def __init__(self, filepath: Path | None = None):
        self.filepath = filepath or cfg.REPLIED_RPID_FILE
        self._replied: set[str] = set()
        self._load()

    def _load(self):
        """从文件加载"""
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                self._replied = set(data.get("replied_rpids", []))
                logger.info(f"已加载 {len(self._replied)} 条已回复记录")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"已回复记录文件损坏，重新初始化: {e}")
                self._replied = set()

    def save(self):
        """持久化到文件"""
        self.filepath.parent.mkdir(exist_ok=True)
        data = {
            "replied_rpids": sorted(self._replied),
            "updated_at": datetime.now().isoformat(),
            "count": len(self._replied),
        }
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_replied(self, rpid: int | str) -> bool:
        """检查是否已回复"""
        return str(rpid) in self._replied

    def mark_replied(self, rpid: int | str):
        """标记为已回复"""
        self._replied.add(str(rpid))

    def __len__(self):
        return len(self._replied)


# ============================================================
# 监控视频列表管理
# ============================================================

class MonitoredVideos:
    """管理 monitored_videos.json"""

    def __init__(self, filepath: Path | None = None):
        self.filepath = filepath or cfg.MONITORED_VIDEOS_FILE
        self._videos: list[dict] = []
        self._load()

    def _load(self):
        """加载监控列表"""
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                self._videos = data.get("videos", [])
                logger.info(f"已加载 {len(self._videos)} 个监控视频")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"监控视频文件损坏: {e}")
                self._videos = []
        else:
            self._videos = []

    def save(self):
        """持久化"""
        self.filepath.parent.mkdir(exist_ok=True)
        data = {
            "videos": self._videos,
            "updated_at": datetime.now().isoformat(),
        }
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_video(self, bvid: str, title: str = "", priority: int = 0, pubdate: int = 0):
        """
        添加视频到监控列表。
        Args:
            bvid: BV号
            title: 视频标题（可选）
            priority: 优先级（0=普通, 1=新上传重点, 2=高优先）
            pubdate: 视频发布时间（Unix时间戳，用于新旧判断）
        """
        # 去重
        for v in self._videos:
            if v["bvid"] == bvid:
                v["priority"] = max(v.get("priority", 0), priority)
                if title:
                    v["title"] = title
                if pubdate:
                    v["pubdate"] = pubdate
                self.save()
                return

        self._videos.append({
            "bvid": bvid,
            "title": title,
            "priority": priority,
            "pubdate": pubdate,
            "added_at": datetime.now().isoformat(),
        })
        self.save()
        logger.info(f"新增监控视频: {bvid} (priority={priority})")

    def remove_video(self, bvid: str):
        """移除视频"""
        self._videos = [v for v in self._videos if v["bvid"] != bvid]
        self.save()

    def get_sorted_videos(self) -> list[dict]:
        """
        获取按优先级排序的视频列表。
        排序：priority 降序 → added_at 降序（新的优先）
        """
        return sorted(
            self._videos,
            key=lambda v: (v.get("priority", 0), v.get("added_at", "")),
            reverse=True,
        )

    def get_new_videos(self, days: int = 3) -> list[dict]:
        """
        获取近 N 天发布的新视频（按优先级排序）。
        Args:
            days: 天数阈值
        Returns:
            list[dict]: 新视频列表
        """
        cutoff = time.time() - days * 86400
        new = [v for v in self._videos if v.get("pubdate", 0) >= cutoff]
        return sorted(
            new,
            key=lambda v: (v.get("priority", 0), v.get("pubdate", 0)),
            reverse=True,
        )

    def __len__(self):
        return len(self._videos)


# ============================================================
# 回复频率控制器
# ============================================================

class RateLimiter:
    """回复频率控制"""

    def __init__(self, max_per_hour: int = 30):
        self.max_per_hour = max_per_hour
        self._timestamps: list[float] = []

    def can_reply(self) -> bool:
        """检查是否可以发送回复"""
        now = time.time()
        # 清理超过1小时的记录
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return len(self._timestamps) < self.max_per_hour

    def record_reply(self):
        """记录一次回复"""
        self._timestamps.append(time.time())

    @property
    def remaining(self) -> bool:
        """本小时剩余可用次数"""
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return self.max_per_hour - len(self._timestamps)


# ============================================================
# 日志记录
# ============================================================

def setup_logging(log_level: str = "INFO"):
    """配置日志"""
    log_path = cfg.get_log_path()
    formatter = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 文件 handler
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    # 控制台 handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger("receptionist")
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def log_reply(bvid: str, rpid: int, user_name: str, comment: str, reply: str, video_title: str = ""):
    """记录单条回复到日志"""
    logger.info(
        f"[回复] bvid={bvid} | 用户={user_name} | "
        f"评论={comment[:50]}... | 回复={reply[:50]}..."
    )
    # 同时写入结构化日志
    log_entry = {
        "time": datetime.now().isoformat(),
        "bvid": bvid,
        "video_title": video_title,
        "rpid": rpid,
        "user_name": user_name,
        "comment": comment,
        "reply": reply,
    }
    log_path = cfg.get_log_path()
    structured_path = log_path.with_suffix(".jsonl")
    with open(structured_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ============================================================
# 评论过滤与优先级排序
# ============================================================

def filter_comments(replies: list[dict], tracker: RepliedTracker, bot_uid: str) -> list[dict]:
    """
    过滤评论，移除已回复、自己的评论、空评论。
    Args:
        replies: 原始评论列表
        tracker: 已回复追踪器
        bot_uid: 当前用户 UID（排除自己的评论）
    Returns:
        list[dict]: 过滤后的评论
    """
    filtered = []
    for r in replies:
        rpid = r["rpid"]
        # 跳过已回复
        if tracker.is_replied(rpid):
            continue
        # 跳过自己的评论
        if str(r["user_mid"]) == str(bot_uid):
            continue
        # 跳过空评论
        if not r["message"].strip():
            continue
        filtered.append(r)
    return filtered


def prioritize_comments(replies: list[dict], video_priority: int = 0) -> list[dict]:
    """
    对评论进行优先级排序。
    优先级规则：
    1. @阿树__atree 的评论（包含 @ 或 提及 UP主名）
    2. 高赞评论（like_count > 5）
    3. 有回复数的评论（说明有人在讨论）
    4. 视频优先级加成
    Args:
        replies: 过滤后的评论列表
        video_priority: 视频优先级
    Returns:
        list[dict]: 排序后的评论
    """
    def score(r: dict) -> int:
        s = 0
        msg = r["message"]
        # @ UP主 或 提及
        if "@" in msg or "阿树" in msg:
            s += 100
        # 高赞
        if r["like_count"] >= 10:
            s += 50
        elif r["like_count"] >= 5:
            s += 30
        elif r["like_count"] >= 1:
            s += 10
        # 有讨论
        if r["reply_count"] > 0:
            s += 20
        # 视频优先级
        s += video_priority * 10
        return s

    return sorted(replies, key=score, reverse=True)


# ============================================================
# 单视频处理流程
# ============================================================

def process_video(
    bvid: str,
    tracker: RepliedTracker,
    rate_limiter: RateLimiter,
    video_priority: int = 0,
    video_title: str = "",
    max_replies: int = 0,
) -> int:
    """
    处理单个视频的评论。
    Args:
        bvid: BV号
        tracker: 已回复追踪器
        rate_limiter: 频率控制器
        video_priority: 视频优先级
        video_title: 视频标题
        max_replies: 本次最大回复数（0=不限，受 rate_limiter 控制）
    Returns:
        int: 实际回复数
    """
    # 静默时段检查
    if cfg.is_quiet_hours():
        logger.info("当前为静默时段，跳过处理")
        return 0

    # 获取 AID
    try:
        aid = api.bvid_to_aid(bvid)
    except api.BiliAPIError as e:
        logger.error(f"BV号转换失败: {bvid}, {e}")
        return 0

    if not video_title:
        try:
            info = api.get_video_info(bvid)
            video_title = info.get("title", "")
        except Exception:
            pass

    # 抓取全部评论（翻页遍历）
    replies = api.fetch_all_comments(aid, mode=api.MODE_TIME)
    if not replies:
        logger.debug(f"无新评论: {bvid}")
        return 0

    # 过滤
    bot_uid = cfg.get_uid()
    filtered = filter_comments(replies, tracker, bot_uid)
    if not filtered:
        logger.debug(f"过滤后无待处理评论: {bvid}")
        return 0

    # 优先级排序
    prioritized = prioritize_comments(filtered, video_priority)

    # 逐条处理
    reply_count = 0
    rconf = cfg.get_receptionist_config()
    delay_min = rconf["reply_delay_min"]
    delay_max = rconf["reply_delay_max"]

    for comment in prioritized:
        # 频率检查
        if not rate_limiter.can_reply():
            logger.warning(f"本小时回复已达上限，停止处理")
            break

        # 最大回复数检查
        if max_replies > 0 and reply_count >= max_replies:
            logger.info(f"已达本次最大回复数 ({max_replies})，停止")
            break

        # 生成回复
        reply_text = generate_unique_reply(
            comment_message=comment["message"],
            comment_user=comment["user_name"],
            video_title=video_title,
        )
        if not reply_text:
            logger.warning(f"回复生成失败: rpid={comment['rpid']}")
            continue

        # 发送回复（作为子评论回复目标评论，而不是新建顶层评论）
        # 顶层评论(root==0): root_rpid 和 parent_rpid 都用自身 rpid
        # 子评论(root!=0): root_rpid 用 root，parent_rpid 用自身 rpid
        root = comment["rpid"] if comment["root"] == 0 else comment["root"]
        parent = comment["rpid"]
        result = api.safe_send_reply(aid, reply_text, parent_rpid=parent, root_rpid=root)
        if not result:
            logger.error(f"回复发送失败: rpid={comment['rpid']}")
            continue

        # 成功
        tracker.mark_replied(comment["rpid"])
        rate_limiter.record_reply()
        reply_count += 1

        log_reply(
            bvid=bvid,
            rpid=comment["rpid"],
            user_name=comment["user_name"],
            comment=comment["message"],
            reply=reply_text,
            video_title=video_title,
        )

        # 随机延迟（防风控）
        delay = random.uniform(delay_min, delay_max)
        logger.debug(f"等待 {delay:.1f}s 后处理下一条...")
        time.sleep(delay)

    return reply_count


# ============================================================
# 主循环
# ============================================================

def run_once(
    tracker: RepliedTracker | None = None,
    videos: MonitoredVideos | None = None,
    max_replies_per_video: int = 0,
    mode: str = "all",
) -> dict:
    """
    执行一轮完整检查。
    Args:
        tracker: 已回复追踪器（可选，自动创建）
        videos: 监控视频列表（可选，自动加载）
        max_replies_per_video: 每个视频最大回复数
        mode: "all"=检查所有视频, "new"=只检查近3天新视频
    Returns:
        dict: {"total_replies": int, "videos_processed": int, "details": list}
    """
    if tracker is None:
        tracker = RepliedTracker()
    if videos is None:
        videos = MonitoredVideos()

    rconf = cfg.get_receptionist_config()
    rate_limiter = RateLimiter(rconf["max_replies_per_hour"])

    # 根据模式选择视频列表
    if mode == "new":
        new_days = rconf.get("new_video_days", 3)
        video_list = videos.get_new_videos(days=new_days)
        logger.info(f"[new模式] 只检查近{new_days}天的新视频，共 {len(video_list)} 个")
    else:
        video_list = videos.get_sorted_videos()
        logger.info(f"[all模式] 检查所有视频，共 {len(video_list)} 个")

    total_replies = 0
    details = []

    for video in video_list:
        bvid = video["bvid"]
        priority = video.get("priority", 0)
        title = video.get("title", "")

        logger.info(f"处理视频: {bvid} (priority={priority}, title={title[:20]}...)")

        count = process_video(
            bvid=bvid,
            tracker=tracker,
            rate_limiter=rate_limiter,
            video_priority=priority,
            video_title=title,
            max_replies=max_replies_per_video,
        )

        total_replies += count
        details.append({"bvid": bvid, "replies": count, "title": title})

        if count > 0:
            # 视频间额外延迟
            time.sleep(random.uniform(2, 5))

    # 持久化
    tracker.save()

    logger.info(f"本轮完成: 处理 {len(details)} 个视频, 发送 {total_replies} 条回复")
    return {
        "total_replies": total_replies,
        "videos_processed": len(details),
        "details": details,
    }


def run_loop(interval_minutes: int | None = None):
    """
    定时循环运行。
    Args:
        interval_minutes: 检查间隔（分钟），默认从配置读取
    """
    rconf = cfg.get_receptionist_config()
    interval = interval_minutes or rconf["check_interval_minutes"]

    logger.info(f"启动接待循环，间隔 {interval} 分钟")

    tracker = RepliedTracker()
    videos = MonitoredVideos()

    # 启动时自动发现视频（如果配置启用）
    rconf = cfg.get_receptionist_config()
    if rconf.get("auto_discover", False):
        uid = cfg.get_uid()
        logger.info(f"自动发现模式：拉取用户 {uid} 的所有视频...")
        try:
            all_videos = api.fetch_all_user_videos(int(uid))
            for v in all_videos:
                videos.add_video(v["bvid"], title=v.get("title", ""), priority=0, pubdate=v.get("created", 0))
            logger.info(f"自动发现完成，当前监控 {len(videos)} 个视频")
        except Exception as e:
            logger.warning(f"自动发现失败: {e}，使用已有监控列表")

    # 启动时检测 Cookie
    if not api.check_cookie_valid():
        logger.error("Cookie 无效，请更新 config.json 后重启")
        return

    logger.info("Cookie 有效，开始接待")

    while True:
        try:
            result = run_once(tracker, videos)
            logger.info(f"本轮结果: {result['total_replies']} 条回复")
        except KeyboardInterrupt:
            logger.info("收到中断信号，保存并退出")
            tracker.save()
            break
        except Exception as e:
            logger.error(f"运行异常: {e}", exc_info=True)

        logger.info(f"等待 {interval} 分钟后再次检查...")
        time.sleep(interval * 60)
