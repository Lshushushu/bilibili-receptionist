"""
bilibili-receptionist 主逻辑模块（防 412 风控版）
核心改动：
- 批量回复模式：攒够 batch_size 条再集中发送
- 固定运行时段：每天 12:00 和 01:00
- 全量扫描后强制冷却 75 分钟
- 412 熔断：立即停止 → 通知 → 暂停 10h → 退出
- 视频间随机延迟 50~100 秒
- 默认 new 模式
"""

import json
import time
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path

import config as cfg
import bilibili_api as api
from bilibili_api import Bili412Error
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
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                self._replied = set(data.get("replied_rpids", []))
                logger.info(f"已加载 {len(self._replied)} 条已回复记录")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"已回复记录文件损坏，重新初始化: {e}")
                self._replied = set()

    def save(self):
        self.filepath.parent.mkdir(exist_ok=True)
        data = {
            "replied_rpids": sorted(self._replied),
            "updated_at": datetime.now().isoformat(),
            "count": len(self._replied),
        }
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def is_replied(self, rpid: int | str) -> bool:
        return str(rpid) in self._replied

    def mark_replied(self, rpid: int | str):
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
        if self.filepath.exists():
            try:
                data = json.loads(self.filepath.read_text(encoding="utf-8"))
                self._videos = data.get("videos", [])
                logger.info(f"已加载 {len(self._videos)} 个监控视频")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning(f"监控视频文件损坏: {e}")
                self._videos = []

    def save(self):
        self.filepath.parent.mkdir(exist_ok=True)
        data = {"videos": self._videos, "updated_at": datetime.now().isoformat()}
        self.filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_video(self, bvid: str, title: str = "", priority: int = 0, pubdate: int = 0):
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
            "bvid": bvid, "title": title, "priority": priority,
            "pubdate": pubdate, "added_at": datetime.now().isoformat(),
        })
        self.save()
        logger.info(f"新增监控视频: {bvid} (priority={priority})")

    def remove_video(self, bvid: str):
        self._videos = [v for v in self._videos if v["bvid"] != bvid]
        self.save()

    def get_sorted_videos(self) -> list[dict]:
        return sorted(self._videos, key=lambda v: (v.get("priority", 0), v.get("added_at", "")), reverse=True)

    def get_new_videos(self, days: int = 3) -> list[dict]:
        cutoff = time.time() - days * 86400
        new = [v for v in self._videos if v.get("pubdate", 0) >= cutoff]
        return sorted(new, key=lambda v: (v.get("priority", 0), v.get("pubdate", 0)), reverse=True)

    def __len__(self):
        return len(self._videos)


# ============================================================
# 回复频率控制器
# ============================================================

class RateLimiter:
    def __init__(self, max_per_hour: int = 30):
        self.max_per_hour = max_per_hour
        self._timestamps: list[float] = []

    def can_reply(self) -> bool:
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return len(self._timestamps) < self.max_per_hour

    def record_reply(self):
        self._timestamps.append(time.time())

    @property
    def remaining(self) -> int:
        now = time.time()
        self._timestamps = [t for t in self._timestamps if now - t < 3600]
        return self.max_per_hour - len(self._timestamps)


# ============================================================
# 日志
# ============================================================

def setup_logging(log_level: str = "INFO"):
    log_path = cfg.get_log_path()
    formatter = logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger = logging.getLogger("receptionist")
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)


def log_reply(bvid: str, rpid: int, user_name: str, comment: str, reply: str, video_title: str = ""):
    logger.info(f"[回复] bvid={bvid} | 用户={user_name} | 评论={comment[:50]}... | 回复={reply[:50]}...")
    log_entry = {
        "time": datetime.now().isoformat(),
        "bvid": bvid, "video_title": video_title,
        "rpid": rpid, "user_name": user_name,
        "comment": comment, "reply": reply,
    }
    log_path = cfg.get_log_path()
    structured_path = log_path.with_suffix(".jsonl")
    with open(structured_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


# ============================================================
# 评论过滤与排序
# ============================================================

def filter_comments(replies: list[dict], tracker: RepliedTracker, bot_uid: str) -> list[dict]:
    filtered = []
    for r in replies:
        if tracker.is_replied(r["rpid"]):
            continue
        if str(r["user_mid"]) == str(bot_uid):
            continue
        if not r["message"].strip():
            continue
        filtered.append(r)
    return filtered


def prioritize_comments(replies: list[dict], video_priority: int = 0) -> list[dict]:
    def score(r: dict) -> int:
        s = 0
        msg = r["message"]
        if "@" in msg or "阿树" in msg:
            s += 100
        if r["like_count"] >= 10:
            s += 50
        elif r["like_count"] >= 5:
            s += 30
        elif r["like_count"] >= 1:
            s += 10
        if r["reply_count"] > 0:
            s += 20
        s += video_priority * 10
        return s
    return sorted(replies, key=score, reverse=True)


# ============================================================
# 批量回复模式
# ============================================================

def _collect_unreplied_comments(
    bvid: str,
    tracker: RepliedTracker,
    max_comments: int = 20,
) -> list[dict]:
    """
    收集单个视频的未回复评论（只抓取，不回复）。
    """
    if api.is_412_triggered():
        return []

    try:
        aid = api.bvid_to_aid(bvid)
    except Bili412Error:
        raise
    except api.BiliAPIError as e:
        logger.error(f"BV号转换失败: {bvid}, {e}")
        return []

    replies = api.fetch_all_comments(aid, mode=api.MODE_TIME)
    if not replies:
        return []

    bot_uid = cfg.get_uid()
    filtered = filter_comments(replies, tracker, bot_uid)
    prioritized = prioritize_comments(filtered)
    return prioritized[:max_comments]


def _send_batch_replies(
    comments: list[dict],
    bvid: str,
    tracker: RepliedTracker,
    rate_limiter: RateLimiter,
    video_title: str = "",
) -> int:
    """
    批量发送回复。
    """
    if api.is_412_triggered():
        return 0

    try:
        aid = api.bvid_to_aid(bvid)
    except Bili412Error:
        raise
    except api.BiliAPIError as e:
        logger.error(f"BV号转换失败: {bvid}, {e}")
        return 0

    rconf = cfg.get_receptionist_config()
    reply_count = 0

    for comment in comments:
        if api.is_412_triggered():
            logger.warning("412 熔断中，停止发送")
            break
        if not rate_limiter.can_reply():
            logger.warning("本小时回复已达上限，停止")
            break

        reply_text = generate_unique_reply(
            comment_message=comment["message"],
            comment_user=comment["user_name"],
            video_title=video_title,
        )
        if not reply_text:
            continue

        root = comment["rpid"] if comment["root"] == 0 else comment["root"]
        parent = comment["rpid"]
        try:
            result = api.safe_send_reply(aid, reply_text, parent_rpid=parent, root_rpid=root)
        except Bili412Error:
            raise

        if not result:
            logger.error(f"回复发送失败: rpid={comment['rpid']}")
            continue

        tracker.mark_replied(comment["rpid"])
        rate_limiter.record_reply()
        reply_count += 1

        log_reply(
            bvid=bvid, rpid=comment["rpid"],
            user_name=comment["user_name"],
            comment=comment["message"], reply=reply_text,
            video_title=video_title,
        )

    return reply_count


# ============================================================
# 单视频处理（批量模式）
# ============================================================

def process_video(
    bvid: str,
    tracker: RepliedTracker,
    rate_limiter: RateLimiter,
    video_priority: int = 0,
    video_title: str = "",
    batch_size: int = 6,
) -> int:
    """
    处理单个视频：收集未回复评论 → 攒够 batch_size 条 → 集中发送。
    """
    if cfg.is_quiet_hours():
        logger.info("当前为静默时段，跳过")
        return 0
    if api.is_412_triggered():
        return 0

    if not video_title:
        try:
            info = api.get_video_info(bvid)
            video_title = info.get("title", "")
        except Bili412Error:
            raise
        except Exception:
            pass

    # 收集未回复评论
    try:
        comments = _collect_unreplied_comments(bvid, tracker)
    except Bili412Error:
        raise

    if not comments:
        logger.debug(f"无待处理评论: {bvid}")
        return 0

    # 只取 batch_size 条
    batch = comments[:batch_size]
    logger.info(f"[{bvid}] 收集到 {len(comments)} 条待回复，本轮处理 {len(batch)} 条")

    # 批量发送
    try:
        count = _send_batch_replies(batch, bvid, tracker, rate_limiter, video_title)
    except Bili412Error:
        raise

    return count


# ============================================================
# 一轮完整扫描
# ============================================================

def run_once(
    tracker: RepliedTracker | None = None,
    videos: MonitoredVideos | None = None,
    mode: str | None = None,
) -> dict:
    """
    执行一轮完整扫描。
    """
    if tracker is None:
        tracker = RepliedTracker()
    if videos is None:
        videos = MonitoredVideos()

    rconf = cfg.get_receptionist_config()
    rate_limiter = RateLimiter(rconf["max_replies_per_hour"])
    batch_size = rconf.get("batch_size", 6)

    # 默认模式从配置读取
    if mode is None:
        mode = rconf.get("default_mode", "new")

    if mode == "new":
        new_days = rconf.get("new_video_days", 3)
        video_list = videos.get_new_videos(days=new_days)
        logger.info(f"[new模式] 检查近{new_days}天新视频，共 {len(video_list)} 个")
    else:
        video_list = videos.get_sorted_videos()
        logger.info(f"[all模式] 检查所有视频，共 {len(video_list)} 个")

    total_replies = 0
    details = []

    for i, video in enumerate(video_list):
        if api.is_412_triggered():
            logger.warning("412 熔断中，停止本轮扫描")
            break

        bvid = video["bvid"]
        priority = video.get("priority", 0)
        title = video.get("title", "")

        logger.info(f"处理视频 [{i+1}/{len(video_list)}]: {bvid} (priority={priority}, title={title[:20]}...)")

        try:
            count = process_video(
                bvid=bvid, tracker=tracker, rate_limiter=rate_limiter,
                video_priority=priority, video_title=title,
                batch_size=batch_size,
            )
        except Bili412Error:
            logger.critical("412 触发，中断本轮")
            break

        total_replies += count
        details.append({"bvid": bvid, "replies": count, "title": title})

        # 视频间随机延迟 50~100 秒
        if i < len(video_list) - 1:
            video_delay = random.uniform(
                rconf.get("video_delay_min", 50),
                rconf.get("video_delay_max", 100),
            )
            logger.info(f"视频间延迟: {video_delay:.0f}s")
            time.sleep(video_delay)

    tracker.save()
    logger.info(f"本轮完成: 处理 {len(details)} 个视频, 发送 {total_replies} 条回复")
    return {"total_replies": total_replies, "videos_processed": len(details), "details": details}


# ============================================================
# 412 熔断处理
# ============================================================

def handle_412_circuit_break():
    """
    412 熔断：通知 → 暂停 10 小时 → 退出
    """
    rconf = cfg.get_receptionist_config()
    pause_hours = rconf.get("pause_on_412_hours", 10)

    logger.critical(
        f"\n{'='*60}\n"
        f"🚨 412 风控触发 — 进入熔断模式\n"
        f"将暂停 {pause_hours} 小时后自动退出。\n"
        f"{'='*60}"
    )

    # TODO: 这里可以接入微信/钉钉/邮件通知
    # notify_412(pause_hours)

    logger.info(f"开始暂停 {pause_hours} 小时 ({pause_hours * 3600} 秒)...")
    time.sleep(pause_hours * 3600)

    logger.info("暂停结束，保存状态并退出程序")
    return


# ============================================================
# 定时调度：每天 12:00 和 01:00
# ============================================================

def _seconds_until_next_run(run_hours: list[int]) -> int:
    """计算距离下一个运行时刻的秒数"""
    now = datetime.now()
    candidates = []
    for hour in run_hours:
        target_today = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target_today > now:
            candidates.append(target_today)
        # 明天的这个时刻
        candidates.append(target_today + timedelta(days=1))

    if not candidates:
        return 3600  # fallback

    next_run = min(candidates)
    delta = (next_run - now).total_seconds()
    return max(int(delta), 1)


def run_loop():
    """
    定时循环：每天 12:00 和 01:00 各执行一轮。
    全量扫描后强制冷却 75 分钟。
    412 触发时立即停止 → 暂停 → 退出。
    """
    rconf = cfg.get_receptionist_config()
    run_hours = rconf.get("run_hours", [12, 1])
    cooldown_minutes = rconf.get("full_scan_cooldown_minutes", 75)
    default_mode = rconf.get("default_mode", "new")

    tracker = RepliedTracker()
    videos = MonitoredVideos()

    # 启动时检测 Cookie
    if not api.check_cookie_valid():
        logger.error("Cookie 无效，请更新 config.json 后重启")
        return

    logger.info(f"Cookie 有效。运行时段: {run_hours}:00，默认模式: {default_mode}")

    # 自动发现
    if rconf.get("auto_discover", False):
        uid = cfg.get_uid()
        logger.info(f"自动发现：拉取用户 {uid} 的视频...")
        try:
            all_videos = api.fetch_all_user_videos(int(uid))
            for v in all_videos:
                videos.add_video(v["bvid"], title=v.get("title", ""), priority=0, pubdate=v.get("created", 0))
            logger.info(f"自动发现完成，监控 {len(videos)} 个视频")
        except Exception as e:
            logger.warning(f"自动发现失败: {e}")

    while True:
        try:
            # 等待到下一个运行时刻
            wait_seconds = _seconds_until_next_run(run_hours)
            next_run = datetime.now() + timedelta(seconds=wait_seconds)
            logger.info(f"下次运行: {next_run.strftime('%Y-%m-%d %H:%M:%S')} (等待 {wait_seconds/60:.0f} 分钟)")
            time.sleep(wait_seconds)

            # 执行一轮
            logger.info("=" * 50)
            logger.info("开始新一轮全量扫描")
            logger.info("=" * 50)

            result = run_once(tracker, videos, mode=default_mode)
            logger.info(f"本轮结果: {result['total_replies']} 条回复")

            # 412 检查
            if api.is_412_triggered():
                handle_412_circuit_break()
                break

            # 全量扫描后强制冷却
            logger.info(f"全量扫描完成，强制冷却 {cooldown_minutes} 分钟...")
            time.sleep(cooldown_minutes * 60)

        except KeyboardInterrupt:
            logger.info("收到中断信号，保存并退出")
            tracker.save()
            break
        except Bili412Error:
            handle_412_circuit_break()
            break
        except Exception as e:
            logger.error(f"运行异常: {e}", exc_info=True)
            time.sleep(300)  # 异常后等 5 分钟再试
