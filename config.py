"""
bilibili-receptionist 配置模块
加载 config.json，提供全局路径常量和配置访问接口。
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime

# ============================================================
# 路径常量
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
REPLIED_RPID_FILE = DATA_DIR / "replied_rpid.json"
MONITORED_VIDEOS_FILE = BASE_DIR / "monitored_videos.json"

# 确保目录存在
LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)


# ============================================================
# 配置加载
# ============================================================
_config_cache: dict | None = None


def load_config(config_path: Path | None = None) -> dict:
    """
    加载配置文件，带缓存。
    Args:
        config_path: 配置文件路径，默认为项目根目录下的 config.json
    Returns:
        dict: 配置字典
    Raises:
        FileNotFoundError: 配置文件不存在
        json.JSONDecodeError: 配置文件 JSON 格式错误
    """
    global _config_cache
    if _config_cache is not None and config_path is None:
        return _config_cache

    path = config_path or CONFIG_FILE
    if not path.exists():
        raise FileNotFoundError(f"配置文件不存在: {path}")

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 始终缓存，确保后续无参调用能命中
    _config_cache = config

    return config


def reload_config() -> dict:
    """强制重新加载配置（清除缓存）"""
    global _config_cache
    _config_cache = None
    return load_config()


# ============================================================
# 便捷访问函数
# ============================================================

def get_cookies() -> dict:
    """
    获取 B站 Cookie 字典，用于 requests 请求。
    Returns:
        dict: {"SESSDATA": "...", "bili_jct": "...", "DedeUserID": "..."}
    Raises:
        KeyError: 缺少必要 Cookie 字段
    """
    config = load_config()
    cookies = config.get("cookies", {})

    required = ["SESSDATA", "bili_jct", "DedeUserID"]
    missing = [k for k in required if k not in cookies]
    if missing:
        raise KeyError(f"config.json 缺少必要 Cookie 字段: {missing}")

    return cookies


def get_cookie_string() -> str:
    """
    获取 Cookie 请求头字符串格式。
    Returns:
        str: "SESSDATA=xxx; bili_jct=xxx; DedeUserID=xxx"
    """
    cookies = get_cookies()
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def get_csrf() -> str:
    """获取 bili_jct（CSRF token）"""
    return get_cookies()["bili_jct"]


def get_uid() -> str:
    """获取当前用户 DedeUserID"""
    return get_cookies()["DedeUserID"]


def get_receptionist_config() -> dict:
    """
    获取接待模块专属配置（带默认值）。
    Returns:
        dict: 包含防 412 风控的全部参数
    """
    config = load_config()
    defaults = {
        "run_hours": [12, 1],
        "full_scan_cooldown_minutes": 75,
        "video_delay_min": 50,
        "video_delay_max": 100,
        "page_delay_min": 5,
        "page_delay_max": 9,
        "page_extra_delay_min": 2,
        "page_extra_delay_max": 4,
        "reply_delay_min": 12,
        "reply_delay_max": 28,
        "batch_size": 6,
        "max_replies_per_hour": 30,
        "quiet_hours": [0, 7],
        "default_reply_style": "warm_healing",
        "bot_name": "荒野小爪",
        "default_mode": "new",
        "auto_discover": True,
        "new_video_days": 3,
        "pause_on_412_hours": 10,
    }
    receptionist = config.get("receptionist", {})
    return {**defaults, **receptionist}


# ============================================================
# 日志文件路径
# ============================================================

def get_log_path(date: datetime | None = None) -> Path:
    """
    获取指定日期的日志文件路径。
    Args:
        date: 日期，默认今天
    Returns:
        Path: logs/receptionist_YYYY-MM-DD.log
    """
    dt = date or datetime.now()
    filename = f"receptionist_{dt.strftime('%Y-%m-%d')}.log"
    return LOG_DIR / filename


# ============================================================
# 配置校验
# ============================================================

def validate_config(config: dict | None = None) -> list[str]:
    """
    校验配置完整性，返回问题列表。空列表表示配置有效。
    Args:
        config: 配置字典，默认自动加载
    Returns:
        list[str]: 问题描述列表，空 = 通过
    """
    if config is None:
        try:
            config = load_config()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            return [str(e)]

    issues = []

    # Cookie 校验
    cookies = config.get("cookies", {})
    for key in ["SESSDATA", "bili_jct", "DedeUserID"]:
        if key not in cookies:
            issues.append(f"缺少 Cookie: {key}")
        elif not cookies[key]:
            issues.append(f"Cookie 为空: {key}")

    # receptionist 配置校验
    rconf = config.get("receptionist", {})
    if rconf:
        delay_min = rconf.get("reply_delay_min", 3)
        delay_max = rconf.get("reply_delay_max", 15)
        if delay_min < 1:
            issues.append(f"reply_delay_min 不能小于 1 秒，当前: {delay_min}")
        if delay_max < delay_min:
            issues.append(f"reply_delay_max ({delay_max}) 不能小于 reply_delay_min ({delay_min})")

        quiet = rconf.get("quiet_hours", [0, 7])
        if len(quiet) != 2:
            issues.append("quiet_hours 必须是 [start, end] 格式")

        max_per_hour = rconf.get("max_replies_per_hour", 30)
        if max_per_hour < 1 or max_per_hour > 100:
            issues.append(f"max_replies_per_hour 范围应为 1-100，当前: {max_per_hour}")

    return issues


# ============================================================
# 工具函数
# ============================================================

def bvid_to_aid(bvid: str) -> int | None:
    """
    BV号转AID（纯字符串算法，不需要 API 调用）。
    基于 B站 BV→AV 转换算法（2024年版本）。
    Args:
        bvid: BV号，如 "BV1xx411c7mD"
    Returns:
        int: AID，转换失败返回 None
    """
    # 此函数保留接口定义，具体实现在 bilibili_api.py 中通过 API 调用更可靠
    # 这里仅作为工具函数占位
    return None


def is_quiet_hours() -> bool:
    """检查当前是否在静默时段"""
    now = datetime.now().hour
    quiet = get_receptionist_config()["quiet_hours"]
    start, end = quiet
    if start <= end:
        return start <= now < end
    else:
        # 跨午夜，如 [23, 7]
        return now >= start or now < end
