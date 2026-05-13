"""
bilibili-receptionist B站 API 封装模块（防 412 风控版）
核心改动：
- 全局请求锁，同一时间只有一个请求在执行
- 随机 User-Agent / Headers 轮换
- 412 Precondition Failed 专用异常 + 熔断机制
- 所有延迟全面随机化
"""

import time
import random
import threading
import logging
import requests
from typing import Optional

import config as cfg

logger = logging.getLogger("receptionist.api")

# ============================================================
# 全局请求锁（保证同一时间只有一个请求）
# ============================================================
_request_lock = threading.Lock()

# ============================================================
# 412 熔断状态
# ============================================================
_412_triggered = False
_412_triggered_time: float = 0


class Bili412Error(Exception):
    """412 Precondition Failed 专用异常"""
    pass


def is_412_triggered() -> bool:
    """检查是否处于 412 熔断状态"""
    return _412_triggered


def reset_412_state():
    """重置 412 状态（测试用或手动恢复）"""
    global _412_triggered, _412_triggered_time
    _412_triggered = False
    _412_triggered_time = 0


# ============================================================
# 随机 User-Agent 池
# ============================================================
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
]

# 随机 Referer 变体
_REFERERS = [
    "https://www.bilibili.com",
    "https://www.bilibili.com/video",
    "https://space.bilibili.com",
    "https://message.bilibili.com",
]


def _random_headers() -> dict:
    """生成随机请求头"""
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Referer": random.choice(_REFERERS),
        "Origin": "https://www.bilibili.com",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Ch-Ua": f'"Chromium";v="{random.randint(120, 126)}", "Not(A:Brand";v="8"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


# ============================================================
# HTTP Session（全局复用）
# ============================================================
_session: requests.Session | None = None

BASE_URL = "https://api.bilibili.com"


def _get_session() -> requests.Session:
    """获取带 Cookie 的 requests.Session（懒初始化）"""
    global _session
    if _session is None:
        _session = requests.Session()
        cookies = cfg.get_cookies()
        _session.cookies.set("SESSDATA", cookies["SESSDATA"], domain=".bilibili.com")
        _session.cookies.set("bili_jct", cookies["bili_jct"], domain=".bilibili.com")
        _session.cookies.set("DedeUserID", cookies["DedeUserID"], domain=".bilibili.com")
    # 每次请求前刷新随机 headers
    _session.headers.update(_random_headers())
    return _session


def reset_session():
    """重置 HTTP Session"""
    global _session
    if _session:
        _session.close()
    _session = None


# ============================================================
# 412 检测 + 熔断
# ============================================================

def _handle_412():
    """处理 412 Precondition Failed：设置熔断状态，抛出异常"""
    global _412_triggered, _412_triggered_time
    _412_triggered = True
    _412_triggered_time = time.time()
    rconf = cfg.get_receptionist_config()
    pause_hours = rconf.get("pause_on_412_hours", 10)
    logger.critical(
        f"\n{'='*60}\n"
        f"🚨🚨🚨 412 Precondition Failed 触发！🚨🚨🚨\n"
        f"B站风控已激活，所有请求立即停止。\n"
        f"将自动暂停 {pause_hours} 小时后退出程序。\n"
        f"请检查 Cookie / 请求频率 / IP 状态。\n"
        f"{'='*60}"
    )
    raise Bili412Error("412 Precondition Failed - B站风控触发")


# ============================================================
# 统一请求封装（带锁 + 412 检测 + 随机延迟）
# ============================================================

def _api_get(path: str, params: dict | None = None) -> dict:
    """
    统一 GET 请求封装。
    - 全局请求锁
    - 随机 Headers
    - 412 检测
    """
    if _412_triggered:
        raise Bili412Error("412 熔断中，拒绝请求")

    with _request_lock:
        session = _get_session()
        url = f"{BASE_URL}{path}"
        try:
            resp = session.get(url, params=params, timeout=20)
        except requests.RequestException as e:
            logger.error(f"网络请求失败: {path} -> {e}")
            raise

        # 412 检测
        if resp.status_code == 412:
            _handle_412()

        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            raise BiliAPIError(data.get("code", -1), data.get("message", "未知错误"), path)
        return data


def _api_post(path: str, data: dict | None = None) -> dict:
    """
    统一 POST 请求封装（自动注入 csrf）。
    - 全局请求锁
    - 随机 Headers
    - 412 检测
    """
    if _412_triggered:
        raise Bili412Error("412 熔断中，拒绝请求")

    with _request_lock:
        session = _get_session()
        url = f"{BASE_URL}{path}"
        if data is None:
            data = {}
        data["csrf"] = cfg.get_csrf()
        try:
            resp = session.post(url, data=data, timeout=20)
        except requests.RequestException as e:
            logger.error(f"网络请求失败: {path} -> {e}")
            raise

        # 412 检测
        if resp.status_code == 412:
            _handle_412()

        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            raise BiliAPIError(result.get("code", -1), result.get("message", "未知错误"), path)
        return result


# ============================================================
# 异常定义
# ============================================================

class BiliAPIError(Exception):
    """B站 API 错误"""
    def __init__(self, code: int, message: str, path: str = ""):
        self.code = code
        self.message = message
        self.path = path
        super().__init__(f"[{code}] {message} (path={path})")


# ============================================================
# 核心 API：BV→AID 转换
# ============================================================

def bvid_to_aid(bvid: str) -> int:
    data = _api_get("/x/web-interface/view", {"bvid": bvid})
    return data["data"]["aid"]


def get_video_info(bvid: str) -> dict:
    data = _api_get("/x/web-interface/view", {"bvid": bvid})
    v = data["data"]
    return {
        "aid": v["aid"],
        "title": v.get("title", ""),
        "desc": v.get("desc", ""),
        "owner": v.get("owner", {}).get("name", ""),
        "pubdate": v.get("pubdate", 0),
    }


# ============================================================
# 核心 API：评论抓取
# ============================================================

MODE_TIME = 3
MODE_HOT = 2
MODE_MIXED = 0
TYPE_VIDEO = 1


def fetch_comments(
    aid: int,
    mode: int = MODE_TIME,
    page: int = 1,
    reply_type: int = TYPE_VIDEO,
) -> dict:
    params = {
        "type": reply_type,
        "oid": aid,
        "mode": mode,
        "next": page,
        "ps": 20,
    }
    data = _api_get("/x/v2/reply/main", params)

    result = {"replies": [], "cursor": {"next": 0, "all_count": 0}}
    replies_data = data.get("data", {})
    cursor = replies_data.get("cursor", {})
    result["cursor"]["next"] = cursor.get("next", 0)
    result["cursor"]["all_count"] = cursor.get("all_count", 0)

    for r in replies_data.get("replies") or []:
        result["replies"].append(_parse_reply(r))
    return result


def fetch_sub_comments(
    aid: int,
    root_rpid: int,
    page: int = 1,
    reply_type: int = TYPE_VIDEO,
) -> list[dict]:
    params = {
        "type": reply_type,
        "oid": aid,
        "root": root_rpid,
        "pn": page,
        "ps": 20,
    }
    data = _api_get("/x/v2/reply/reply", params)
    replies = []
    for r in data.get("data", {}).get("replies") or []:
        replies.append(_parse_reply(r))
    return replies


def _parse_reply(raw: dict) -> dict:
    member = raw.get("member", {})
    content = raw.get("content", {})
    return {
        "rpid": raw.get("rpid", 0),
        "rpid_str": str(raw.get("rpid", 0)),
        "user_mid": member.get("mid", 0),
        "user_name": member.get("uname", ""),
        "message": content.get("message", ""),
        "reply_time": raw.get("ctime", 0),
        "like_count": raw.get("like", 0),
        "reply_count": raw.get("rcount", 0),
        "root": raw.get("root", 0),
        "parent": raw.get("parent", 0),
    }


# ============================================================
# 核心 API：回复发送
# ============================================================

def send_reply(
    aid: int,
    message: str,
    parent_rpid: int = 0,
    root_rpid: int = 0,
    reply_type: int = TYPE_VIDEO,
) -> dict:
    data = {
        "type": reply_type,
        "oid": aid,
        "message": message,
    }
    if parent_rpid > 0:
        data["root"] = root_rpid
        data["parent"] = parent_rpid

    result = _api_post("/x/v2/reply/add", data)
    rpid = result.get("data", {}).get("rpid", 0)
    logger.info(f"回复发送成功: aid={aid}, rpid={rpid}, message={message[:30]}...")
    return {"rpid": rpid, "rpid_str": str(rpid)}


# ============================================================
# 核心 API：获取用户视频列表
# ============================================================

def fetch_user_videos(
    mid: int,
    page: int = 1,
    page_size: int = 30,
    keyword: str = "",
) -> dict:
    try:
        result = _fetch_user_videos_via_space(mid, page, page_size)
        if result.get("videos"):
            return result
    except Exception:
        pass
    return _fetch_user_videos_via_search(mid, page, page_size, keyword)


def _fetch_user_videos_via_space(mid: int, page: int, page_size: int) -> dict:
    params = {"mid": mid, "ps": page_size, "pn": page, "order": "pubdate"}
    data = _api_get("/x/space/arc/search", params)
    vlist_data = data.get("data", {}).get("list", {}).get("vlist", [])
    page_info = data.get("data", {}).get("page", {})
    videos = []
    for v in vlist_data:
        videos.append({
            "bvid": v.get("bvid", ""),
            "title": v.get("title", ""),
            "pubdate": v.get("created", 0),
        })
    return {
        "videos": videos,
        "total": page_info.get("count", 0),
        "page": page_info.get("pn", 1),
        "page_count": page_info.get("count", 0) // page_size + 1,
    }


def _fetch_user_videos_via_search(mid: int, page: int, page_size: int, keyword: str = "") -> dict:
    if not keyword:
        try:
            nav_data = _api_get("/x/web-interface/nav")
            keyword = nav_data.get("data", {}).get("uname", str(mid))
        except Exception:
            keyword = str(mid)
    params = {
        "keyword": keyword,
        "search_type": "video",
        "page": page,
        "pagesize": min(page_size, 50),
        "order": "pubdate",
    }
    data = _api_get("/x/web-interface/search/type", params)
    results = data.get("data", {}).get("result", []) or []
    videos = []
    for v in results:
        if str(v.get("mid", 0)) == str(mid):
            title = v.get("title", "").replace('<em class="keyword">', "").replace("</em>", "")
            videos.append({"bvid": v.get("bvid", ""), "title": title, "pubdate": v.get("pubdate", 0)})
    return {"videos": videos, "total": len(videos), "page": page, "page_count": 5}


def fetch_all_user_videos(mid: int, max_pages: int = 10, keyword: str = "") -> list[dict]:
    all_videos = []
    for page in range(1, max_pages + 1):
        try:
            result = fetch_user_videos(mid, page=page, keyword=keyword)
        except Exception as e:
            logger.warning(f"获取用户视频第{page}页失败: {e}")
            break
        videos = result.get("videos", [])
        if not videos:
            break
        all_videos.extend(videos)
        if page >= result.get("page_count", 1):
            break
        time.sleep(random.uniform(0.5, 1.0))
    logger.info(f"用户 {mid} 共 {len(all_videos)} 个视频")
    return all_videos


# ============================================================
# Cookie 有效性检测
# ============================================================

def check_cookie_valid() -> bool:
    try:
        _api_get("/x/web-interface/nav")
        return True
    except (BiliAPIError, requests.RequestException) as e:
        logger.warning(f"Cookie 检测失败: {e}")
        return False


# ============================================================
# 翻页抓取评论（带防 412 延迟）
# ============================================================

def fetch_all_comments(
    aid: int,
    mode: int = MODE_TIME,
    reply_type: int = TYPE_VIDEO,
    max_pages: int = 10,
) -> list[dict]:
    """
    翻页抓取所有评论。
    延迟策略：每页间 5~9 秒 + 额外随机 2~4 秒
    """
    rconf = cfg.get_receptionist_config()
    page_min = rconf.get("page_delay_min", 5)
    page_max = rconf.get("page_delay_max", 9)
    extra_min = rconf.get("page_extra_delay_min", 2)
    extra_max = rconf.get("page_extra_delay_max", 4)

    all_replies = []
    for page in range(1, max_pages + 1):
        if _412_triggered:
            logger.warning("412 熔断中，停止翻页")
            break

        try:
            result = fetch_comments(aid, mode, page, reply_type)
        except Bili412Error:
            raise
        except Exception as e:
            logger.warning(f"抓取第{page}页评论失败: {e}")
            break

        replies = result.get("replies", [])
        if not replies:
            break
        all_replies.extend(replies)
        logger.debug(f"第{page}页: {len(replies)}条评论")

        next_page = result.get("cursor", {}).get("next", 0)
        if next_page == 0:
            break

        # 页间延迟：5~9 秒
        page_delay = random.uniform(page_min, page_max)
        # 额外随机延迟：2~4 秒
        extra_delay = random.uniform(extra_min, extra_max)
        total_delay = page_delay + extra_delay
        logger.debug(f"页间延迟: {total_delay:.1f}s (page={page_delay:.1f} + extra={extra_delay:.1f})")
        time.sleep(total_delay)

    logger.info(f"共抓取 {len(all_replies)} 条评论 (aid={aid})")
    return all_replies


# ============================================================
# 带重试的安全封装
# ============================================================

def safe_fetch_comments(
    aid: int,
    mode: int = MODE_TIME,
    page: int = 1,
    max_retries: int = 3,
) -> dict | None:
    for attempt in range(max_retries):
        try:
            return fetch_comments(aid, mode, page)
        except Bili412Error:
            raise
        except BiliAPIError as e:
            if e.code == -101:
                logger.error("Cookie 已过期，请更新 config.json")
                return None
            logger.warning(f"评论抓取失败 (attempt {attempt+1}/{max_retries}): {e}")
        except requests.RequestException as e:
            logger.warning(f"网络错误 (attempt {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            time.sleep(random.uniform(2, 5))
    logger.error(f"评论抓取最终失败: aid={aid}")
    return None


def safe_send_reply(
    aid: int,
    message: str,
    parent_rpid: int = 0,
    root_rpid: int = 0,
    max_retries: int = 2,
) -> dict | None:
    rconf = cfg.get_receptionist_config()
    delay_min = rconf.get("reply_delay_min", 12)
    delay_max = rconf.get("reply_delay_max", 28)

    for attempt in range(max_retries):
        try:
            result = send_reply(aid, message, parent_rpid, root_rpid)
            # 发送成功后随机等待 12~28 秒
            reply_wait = random.uniform(delay_min, delay_max)
            logger.debug(f"回复后等待 {reply_wait:.1f}s")
            time.sleep(reply_wait)
            return result
        except Bili412Error:
            raise
        except BiliAPIError as e:
            if e.code == -101:
                logger.error("Cookie 已过期，停止发送")
                return None
            if e.code == 12017:
                logger.warning("发送频率过高，等待后重试")
                time.sleep(random.uniform(15, 30))
                continue
            logger.warning(f"回复发送失败 (attempt {attempt+1}/{max_retries}): {e}")
        except requests.RequestException as e:
            logger.warning(f"网络错误 (attempt {attempt+1}/{max_retries}): {e}")
        if attempt < max_retries - 1:
            time.sleep(random.uniform(3, 8))

    logger.error(f"回复发送最终失败: aid={aid}, message={message[:30]}...")
    return None
