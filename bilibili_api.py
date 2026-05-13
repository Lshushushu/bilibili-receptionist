"""
bilibili-receptionist B站 API 封装模块
提供评论抓取、回复发送、BV号转换等核心接口。
所有 HTTP 请求统一走 requests.Session，自动注入 Cookie。
"""

import time
import random
import logging
import requests
from typing import Optional

import config as cfg

logger = logging.getLogger("receptionist.api")

# ============================================================
# HTTP Session（全局复用）
# ============================================================
_session: requests.Session | None = None

# B站 API 基础地址
BASE_URL = "https://api.bilibili.com"

# 默认请求头
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
    "Origin": "https://www.bilibili.com",
}


def _get_session() -> requests.Session:
    """获取带 Cookie 的 requests.Session（懒初始化）"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update(DEFAULT_HEADERS)
        cookies = cfg.get_cookies()
        _session.cookies.set("SESSDATA", cookies["SESSDATA"], domain=".bilibili.com")
        _session.cookies.set("bili_jct", cookies["bili_jct"], domain=".bilibili.com")
        _session.cookies.set("DedeUserID", cookies["DedeUserID"], domain=".bilibili.com")
    return _session


def reset_session():
    """重置 HTTP Session（Cookie 过期时调用）"""
    global _session
    if _session:
        _session.close()
    _session = None


def _api_get(path: str, params: dict | None = None) -> dict:
    """
    统一 GET 请求封装。
    Returns:
        dict: API 响应 JSON
    Raises:
        BiliAPIError: API 返回非 0 code
        requests.RequestException: 网络错误
    """
    session = _get_session()
    url = f"{BASE_URL}{path}"
    resp = session.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise BiliAPIError(data.get("code", -1), data.get("message", "未知错误"), path)
    return data


def _api_post(path: str, data: dict | None = None) -> dict:
    """
    统一 POST 请求封装（自动注入 csrf）。
    Returns:
        dict: API 响应 JSON
    Raises:
        BiliAPIError: API 返回非 0 code
        requests.RequestException: 网络错误
    """
    session = _get_session()
    url = f"{BASE_URL}{path}"
    if data is None:
        data = {}
    data["csrf"] = cfg.get_csrf()
    resp = session.post(url, data=data, timeout=15)
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
    """
    BV号转 AID。
    Args:
        bvid: BV号，如 "BV1xx411c7mD"
    Returns:
        int: 视频 AID
    Raises:
        BiliAPIError: BV号无效或视频不存在
    """
    data = _api_get("/x/web-interface/view", {"bvid": bvid})
    return data["data"]["aid"]


def get_video_info(bvid: str) -> dict:
    """
    获取视频基本信息。
    Args:
        bvid: BV号
    Returns:
        dict: {"aid": int, "title": str, "desc": str, "owner": str, "pubdate": int}
    """
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

# 评论排序模式
MODE_TIME = 3      # 按时间
MODE_HOT = 2       # 按热度
MODE_MIXED = 0     # 综合

# 评论类型（type 参数）
TYPE_VIDEO = 1     # 视频


def fetch_comments(
    aid: int,
    mode: int = MODE_TIME,
    page: int = 1,
    reply_type: int = TYPE_VIDEO,
) -> dict:
    """
    获取视频评论列表（主评论，不含子评论）。
    Args:
        aid: 视频 AID
        mode: 排序模式（MODE_TIME=3, MODE_HOT=2, MODE_MIXED=0）
        page: 页码（从 1 开始）
        reply_type: 评论类型，默认 1（视频）
    Returns:
        dict: {
            "replies": list[dict],   # 评论列表，可能为空
            "cursor": {
                "next": int,         # 下一页页码，0 表示无更多
                "all_count": int     # 总评论数
            }
        }
    """
    params = {
        "type": reply_type,
        "oid": aid,
        "mode": mode,
        "next": page,
        "ps": 20,  # 每页条数
    }
    data = _api_get("/x/v2/reply/main", params)

    result = {
        "replies": [],
        "cursor": {"next": 0, "all_count": 0},
    }

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
    """
    获取某条主评论下的子评论（楼中楼）。
    Args:
        aid: 视频 AID
        root_rpid: 主评论 rpid
        page: 页码
        reply_type: 评论类型
    Returns:
        list[dict]: 子评论列表
    """
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
    """
    解析单条评论原始数据为标准格式。
    Args:
        raw: API 返回的单条评论 dict
    Returns:
        dict: 标准化评论结构
    """
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
    """
    发送评论回复。
    Args:
        aid: 视频 AID
        message: 回复内容
        parent_rpid: 父评论 rpid（回复子评论时使用）
        root_rpid: 根评论 rpid（回复子评论时使用）
        reply_type: 评论类型
    Returns:
        dict: {"rpid": int, "rpid_str": str}  发送成功后的评论 ID
    Raises:
        BiliAPIError: 发送失败
    """
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
    return {
        "rpid": rpid,
        "rpid_str": str(rpid),
    }


# ============================================================
# 核心 API：获取用户视频列表
# ============================================================

def fetch_user_videos(
    mid: int,
    page: int = 1,
    page_size: int = 30,
    keyword: str = "",
) -> dict:
    """
    获取用户空间视频列表（通过搜索 API，兼容 B站 Wbi 反爬）。
    Args:
        mid: 用户 UID
        page: 页码（从1开始）
        page_size: 每页数量
        keyword: 搜索关键词（默认用用户名搜）
    Returns:
        dict: {
            "videos": [{"bvid": str, "title": str, "pubdate": int}],
            "total": int,
            "page": int,
            "page_count": int
        }
    """
    # 优先尝试 space API（可能被反爬）
    try:
        result = _fetch_user_videos_via_space(mid, page, page_size)
        if result.get("videos"):
            return result
    except Exception:
        pass

    # 降级到搜索 API
    return _fetch_user_videos_via_search(mid, page, page_size, keyword)


def _fetch_user_videos_via_space(mid: int, page: int, page_size: int) -> dict:
    """通过 space API 获取视频（可能被 Wbi 反爬）"""
    params = {
        "mid": mid,
        "ps": page_size,
        "pn": page,
        "order": "pubdate",
    }
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
    """通过搜索 API 获取指定用户的视频"""
    if not keyword:
        # 获取用户名作为搜索关键词
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
            title = v.get("title", "")
            # 清除搜索高亮标签
            title = title.replace("<em class=\"keyword\">", "").replace("</em>", "")
            videos.append({
                "bvid": v.get("bvid", ""),
                "title": title,
                "pubdate": v.get("pubdate", 0),
            })

    return {
        "videos": videos,
        "total": len(videos),
        "page": page,
        "page_count": 5,  # 搜索 API 不返回总页数，给个保守值
    }


def fetch_all_user_videos(mid: int, max_pages: int = 10, keyword: str = "") -> list[dict]:
    """
    翻页获取用户所有视频。
    Args:
        mid: 用户 UID
        max_pages: 最大翻页数
        keyword: 搜索关键词
    Returns:
        list[dict]: 所有视频列表
    """
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
# 辅助：Cookie 有效性检测
# ============================================================

def check_cookie_valid() -> bool:
    """
    检测当前 Cookie 是否有效。
    通过请求用户信息接口判断。
    Returns:
        bool: True 有效，False 已过期
    """
    try:
        _api_get("/x/web-interface/nav")
        return True
    except (BiliAPIError, requests.RequestException) as e:
        logger.warning(f"Cookie 检测失败: {e}")
        return False


# ============================================================
# 辅助：带重试的请求封装
# ============================================================

def fetch_all_comments(
    aid: int,
    mode: int = MODE_TIME,
    reply_type: int = TYPE_VIDEO,
    max_pages: int = 10,
) -> list[dict]:
    """
    翻页抓取所有评论（主评论）。
    Args:
        aid: 视频 AID
        mode: 排序模式
        reply_type: 评论类型
        max_pages: 最大翻页数（防止无限翻页，默认10页=200条评论）
    Returns:
        list[dict]: 所有页面的评论合并列表
    """
    all_replies = []
    for page in range(1, max_pages + 1):
        try:
            result = fetch_comments(aid, mode, page, reply_type)
        except Exception as e:
            logger.warning(f"抓取第{page}页评论失败: {e}")
            break

        replies = result.get("replies", [])
        if not replies:
            break

        all_replies.extend(replies)
        logger.debug(f"第{page}页: {len(replies)}条评论")

        # cursor.next == 0 表示没有更多页
        next_page = result.get("cursor", {}).get("next", 0)
        if next_page == 0:
            break

        # 页间延迟（防风控）
        time.sleep(random.uniform(0.5, 1.5))

    logger.info(f"共抓取 {len(all_replies)} 条评论 (aid={aid})")
    return all_replies


def safe_fetch_comments(
    aid: int,
    mode: int = MODE_TIME,
    page: int = 1,
    max_retries: int = 3,
) -> dict | None:
    """
    带重试的评论抓取。
    Args:
        aid: 视频 AID
        mode: 排序模式
        page: 页码
        max_retries: 最大重试次数
    Returns:
        dict: 评论数据，失败返回 None
    """
    for attempt in range(max_retries):
        try:
            return fetch_comments(aid, mode, page)
        except BiliAPIError as e:
            if e.code == -101:
                logger.error("Cookie 已过期，请更新 config.json")
                return None
            logger.warning(f"评论抓取失败 (attempt {attempt+1}/{max_retries}): {e}")
        except requests.RequestException as e:
            logger.warning(f"网络错误 (attempt {attempt+1}/{max_retries}): {e}")

        if attempt < max_retries - 1:
            wait = random.uniform(1, 3)
            time.sleep(wait)

    logger.error(f"评论抓取最终失败: aid={aid}, 已重试 {max_retries} 次")
    return None


def safe_send_reply(
    aid: int,
    message: str,
    parent_rpid: int = 0,
    root_rpid: int = 0,
    max_retries: int = 2,
) -> dict | None:
    """
    带重试的回复发送。
    Args:
        aid: 视频 AID
        message: 回复内容
        parent_rpid: 父评论 rpid
        root_rpid: 根评论 rpid
        max_retries: 最大重试次数
    Returns:
        dict: 发送结果，失败返回 None
    """
    for attempt in range(max_retries):
        try:
            return send_reply(aid, message, parent_rpid, root_rpid)
        except BiliAPIError as e:
            if e.code == -101:
                logger.error("Cookie 已过期，停止发送")
                return None
            if e.code == 12017:
                logger.warning(f"发送频率过高，等待后重试")
                time.sleep(random.uniform(10, 20))
                continue
            logger.warning(f"回复发送失败 (attempt {attempt+1}/{max_retries}): {e}")
        except requests.RequestException as e:
            logger.warning(f"网络错误 (attempt {attempt+1}/{max_retries}): {e}")

        if attempt < max_retries - 1:
            time.sleep(random.uniform(2, 5))

    logger.error(f"回复发送最终失败: aid={aid}, message={message[:30]}...")
    return None
