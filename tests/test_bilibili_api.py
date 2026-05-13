"""
bilibili_api.py 回归测试
全部使用 Mock 模拟 HTTP 请求，不依赖真实网络。
覆盖：BV转换、评论抓取、子评论、回复发送、Cookie检测、重试机制、错误处理。
"""

import json
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
from requests.exceptions import ConnectionError, Timeout

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
import bilibili_api as api
from bilibili_api import BiliAPIError


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置 session 和 config 缓存"""
    api.reset_session()
    cfg._config_cache = {
        "cookies": {
            "SESSDATA": "test_sess",
            "bili_jct": "test_csrf",
            "DedeUserID": "12345"
        }
    }
    yield
    api.reset_session()
    cfg._config_cache = None


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    """构造 mock HTTP 响应"""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _make_reply(rpid: int, message: str = "test", uname: str = "user1",
                like: int = 0, rcount: int = 0, ctime: int = 1700000000,
                mid: int = 100, root: int = 0, parent: int = 0) -> dict:
    """构造单条评论原始数据"""
    return {
        "rpid": rpid,
        "member": {"mid": mid, "uname": uname},
        "content": {"message": message},
        "like": like,
        "rcount": rcount,
        "ctime": ctime,
        "root": root,
        "parent": parent,
    }


# ============================================================
# BV号转换测试
# ============================================================

class TestBvidToAid:

    @patch("bilibili_api._get_session")
    def test_success(self, mock_get_session):
        """正常 BV→AID 转换"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "message": "0",
            "data": {"aid": 12345678}
        })
        mock_get_session.return_value = session

        aid = api.bvid_to_aid("BV1xx411c7mD")
        assert aid == 12345678

    @patch("bilibili_api._get_session")
    def test_invalid_bvid(self, mock_get_session):
        """无效 BV号抛出 BiliAPIError"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": -400,
            "message": "请求错误",
            "data": None
        })
        mock_get_session.return_value = session

        with pytest.raises(BiliAPIError) as exc_info:
            api.bvid_to_aid("BV_INVALID")
        assert exc_info.value.code == -400


# ============================================================
# 视频信息测试
# ============================================================

class TestGetVideoInfo:

    @patch("bilibili_api._get_session")
    def test_success(self, mock_get_session):
        """正常获取视频信息"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "data": {
                "aid": 9999,
                "title": "测试视频",
                "desc": "描述",
                "owner": {"name": "UP主"},
                "pubdate": 1700000000,
            }
        })
        mock_get_session.return_value = session

        info = api.get_video_info("BV1test")
        assert info["aid"] == 9999
        assert info["title"] == "测试视频"
        assert info["owner"] == "UP主"


# ============================================================
# 评论抓取测试
# ============================================================

class TestFetchComments:

    @patch("bilibili_api._get_session")
    def test_fetch_one_page(self, mock_get_session):
        """抓取单页评论"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "data": {
                "replies": [
                    _make_reply(1001, "好治愈～", "用户A", like=10),
                    _make_reply(1002, "看了三遍", "用户B", like=5, rcount=2),
                ],
                "cursor": {"next": 2, "all_count": 50},
            }
        })
        mock_get_session.return_value = session

        result = api.fetch_comments(12345)
        assert len(result["replies"]) == 2
        assert result["replies"][0]["rpid"] == 1001
        assert result["replies"][0]["message"] == "好治愈～"
        assert result["replies"][0]["like_count"] == 10
        assert result["replies"][1]["reply_count"] == 2
        assert result["cursor"]["next"] == 2
        assert result["cursor"]["all_count"] == 50

    @patch("bilibili_api._get_session")
    def test_fetch_empty_replies(self, mock_get_session):
        """评论为空时返回空列表"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "data": {
                "replies": None,
                "cursor": {"next": 0, "all_count": 0},
            }
        })
        mock_get_session.return_value = session

        result = api.fetch_comments(12345)
        assert result["replies"] == []
        assert result["cursor"]["next"] == 0

    @patch("bilibili_api._get_session")
    def test_fetch_with_mode_hot(self, mock_get_session):
        """按热度排序"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "data": {"replies": [], "cursor": {"next": 0, "all_count": 0}},
        })
        mock_get_session.return_value = session

        api.fetch_comments(12345, mode=api.MODE_HOT)
        call_args = session.get.call_args
        assert call_args[1]["params"]["mode"] == 2

    @patch("bilibili_api._get_session")
    def test_api_error_raised(self, mock_get_session):
        """API 返回错误码时抛出 BiliAPIError"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": -412,
            "message": "请求被拦截"
        })
        mock_get_session.return_value = session

        with pytest.raises(BiliAPIError, match="-412"):
            api.fetch_comments(12345)


# ============================================================
# 子评论抓取测试
# ============================================================

class TestFetchSubComments:

    @patch("bilibili_api._get_session")
    def test_fetch_sub_comments(self, mock_get_session):
        """抓取子评论"""
        session = MagicMock()
        session.get.return_value = _mock_response({
            "code": 0,
            "data": {
                "replies": [
                    _make_reply(2001, "回复1", "用户C", root=1001, parent=1001),
                    _make_reply(2002, "回复2", "用户D", root=1001, parent=2001),
                ]
            }
        })
        mock_get_session.return_value = session

        result = api.fetch_sub_comments(12345, root_rpid=1001)
        assert len(result) == 2
        assert result[0]["root"] == 1001
        assert result[1]["parent"] == 2001


# ============================================================
# 回复发送测试
# ============================================================

class TestSendReply:

    @patch("bilibili_api._get_session")
    def test_send_root_reply(self, mock_get_session):
        """发送主评论回复（无 parent/root）"""
        session = MagicMock()
        session.post.return_value = _mock_response({
            "code": 0,
            "data": {"rpid": 3001}
        })
        mock_get_session.return_value = session

        result = api.send_reply(12345, "谢谢你的支持～🌲")
        assert result["rpid"] == 3001
        assert result["rpid_str"] == "3001"

        # 验证 POST 参数
        call_args = session.post.call_args
        post_data = call_args[1]["data"]
        assert post_data["message"] == "谢谢你的支持～🌲"
        assert post_data["csrf"] == "test_csrf"
        assert "root" not in post_data
        assert "parent" not in post_data

    @patch("bilibili_api._get_session")
    def test_send_sub_reply(self, mock_get_session):
        """发送子评论回复（带 parent/root）"""
        session = MagicMock()
        session.post.return_value = _mock_response({
            "code": 0,
            "data": {"rpid": 3002}
        })
        mock_get_session.return_value = session

        result = api.send_reply(12345, "也祝你开心", parent_rpid=2001, root_rpid=1001)
        assert result["rpid"] == 3002

        call_args = session.post.call_args
        post_data = call_args[1]["data"]
        assert post_data["root"] == 1001
        assert post_data["parent"] == 2001

    @patch("bilibili_api._get_session")
    def test_send_reply_failure(self, mock_get_session):
        """发送失败抛出 BiliAPIError"""
        session = MagicMock()
        session.post.return_value = _mock_response({
            "code": 12017,
            "message": "发送频率过高"
        })
        mock_get_session.return_value = session

        with pytest.raises(BiliAPIError, match="12017"):
            api.send_reply(12345, "测试")


# ============================================================
# Cookie 检测测试
# ============================================================

class TestCheckCookieValid:

    @patch("bilibili_api._get_session")
    def test_cookie_valid(self, mock_get_session):
        """Cookie 有效时返回 True"""
        session = MagicMock()
        session.get.return_value = _mock_response({"code": 0, "data": {"isLogin": True}})
        mock_get_session.return_value = session

        assert api.check_cookie_valid() is True

    @patch("bilibili_api._get_session")
    def test_cookie_expired(self, mock_get_session):
        """Cookie 过期时返回 False"""
        session = MagicMock()
        session.get.return_value = _mock_response({"code": -101, "message": "未登录"})
        mock_get_session.return_value = session

        assert api.check_cookie_valid() is False

    @patch("bilibili_api._get_session")
    def test_network_error(self, mock_get_session):
        """网络错误时返回 False"""
        session = MagicMock()
        session.get.side_effect = ConnectionError("连接超时")
        mock_get_session.return_value = session

        assert api.check_cookie_valid() is False


# ============================================================
# 重试机制测试
# ============================================================

class TestSafeFetchComments:

    @patch("bilibili_api._get_session")
    @patch("bilibili_api.time.sleep")
    def test_retry_on_network_error(self, mock_sleep, mock_get_session):
        """网络错误时自动重试"""
        session = MagicMock()
        # 前两次失败，第三次成功
        session.get.side_effect = [
            ConnectionError("timeout"),
            ConnectionError("timeout"),
            _mock_response({"code": 0, "data": {"replies": [], "cursor": {"next": 0}}}),
        ]
        mock_get_session.return_value = session

        result = api.safe_fetch_comments(12345)
        assert result is not None
        assert session.get.call_count == 3

    @patch("bilibili_api._get_session")
    @patch("bilibili_api.time.sleep")
    def test_return_none_on_cookie_expired(self, mock_sleep, mock_get_session):
        """Cookie 过期时直接返回 None，不重试"""
        session = MagicMock()
        session.get.return_value = _mock_response({"code": -101, "message": "未登录"})
        mock_get_session.return_value = session

        result = api.safe_fetch_comments(12345)
        assert result is None
        assert session.get.call_count == 1  # 不重试

    @patch("bilibili_api._get_session")
    @patch("bilibili_api.time.sleep")
    def test_return_none_after_max_retries(self, mock_sleep, mock_get_session):
        """超过最大重试次数返回 None"""
        session = MagicMock()
        session.get.side_effect = ConnectionError("timeout")
        mock_get_session.return_value = session

        result = api.safe_fetch_comments(12345, max_retries=2)
        assert result is None
        assert session.get.call_count == 2


class TestSafeSendReply:

    @patch("bilibili_api._get_session")
    @patch("bilibili_api.time.sleep")
    def test_retry_on_rate_limit(self, mock_sleep, mock_get_session):
        """频率限制时重试"""
        session = MagicMock()
        session.post.side_effect = [
            _mock_response({"code": 12017, "message": "频率限制"}).__getitem__,
            # 让第一次 raise BiliAPIError，第二次成功
        ]
        # 更精确的 mock：第一次返回错误，第二次返回成功
        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _mock_response({"code": 12017, "message": "频率限制"})
            return _mock_response({"code": 0, "data": {"rpid": 4001}})

        session.post.side_effect = side_effect
        mock_get_session.return_value = session

        result = api.safe_send_reply(12345, "测试回复")
        assert result is not None
        assert result["rpid"] == 4001

    @patch("bilibili_api._get_session")
    @patch("bilibili_api.time.sleep")
    def test_return_none_on_cookie_expired(self, mock_sleep, mock_get_session):
        """Cookie 过期时直接返回 None"""
        session = MagicMock()
        session.post.return_value = _mock_response({"code": -101, "message": "未登录"})
        mock_get_session.return_value = session

        result = api.safe_send_reply(12345, "测试")
        assert result is None


# ============================================================
# Session 管理测试
# ============================================================

class TestSession:

    def test_session_lazily_created(self):
        """Session 懒创建"""
        assert api._session is None
        with patch("bilibili_api.requests.Session") as MockSession:
            mock_s = MagicMock()
            MockSession.return_value = mock_s
            s = api._get_session()
            assert s is mock_s
            assert api._session is mock_s

    def test_reset_session(self):
        """reset_session 清除 session"""
        with patch("bilibili_api.requests.Session") as MockSession:
            mock_s = MagicMock()
            MockSession.return_value = mock_s
            api._get_session()
            assert api._session is not None
            api.reset_session()
            assert api._session is None
            mock_s.close.assert_called_once()


# ============================================================
# _parse_reply 测试
# ============================================================

class TestParseReply:

    def test_parse_complete_reply(self):
        """解析完整评论数据"""
        raw = _make_reply(5001, "很棒！", "测试用户", like=15, rcount=3,
                          ctime=1700000000, mid=999, root=0, parent=0)
        result = api._parse_reply(raw)
        assert result["rpid"] == 5001
        assert result["rpid_str"] == "5001"
        assert result["user_mid"] == 999
        assert result["user_name"] == "测试用户"
        assert result["message"] == "很棒！"
        assert result["like_count"] == 15
        assert result["reply_count"] == 3
        assert result["reply_time"] == 1700000000

    def test_parse_missing_fields(self):
        """缺失字段时使用默认值"""
        raw = {"rpid": 6001}
        result = api._parse_reply(raw)
        assert result["rpid"] == 6001
        assert result["user_name"] == ""
        assert result["message"] == ""
        assert result["like_count"] == 0
