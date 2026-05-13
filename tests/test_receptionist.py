"""
receptionist.py 回归测试
覆盖：RepliedTracker、MonitoredVideos、RateLimiter、评论过滤/排序、单视频处理。
全部使用 Mock，不依赖真实网络。
"""

import json
import time
import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
from receptionist import (
    RepliedTracker,
    MonitoredVideos,
    RateLimiter,
    filter_comments,
    prioritize_comments,
    process_video,
    run_once,
)


@pytest.fixture(autouse=True)
def reset_state():
    """每个测试前重置状态"""
    cfg._config_cache = {
        "cookies": {
            "SESSDATA": "test_sess",
            "bili_jct": "test_csrf",
            "DedeUserID": "12345"
        },
        "receptionist": {
            "reply_delay_min": 0.01,
            "reply_delay_max": 0.02,
            "max_replies_per_hour": 30,
            "quiet_hours": [25, 26],  # 确保不在静默时段
        }
    }
    yield
    cfg._config_cache = None


def _make_reply(rpid: int, msg: str = "好治愈", uname: str = "user",
                mid: int = 100, like: int = 0, rcount: int = 0) -> dict:
    return {
        "rpid": rpid, "rpid_str": str(rpid),
        "user_mid": mid, "user_name": uname,
        "message": msg, "reply_time": 1700000000,
        "like_count": like, "reply_count": rcount,
        "root": 0, "parent": 0,
    }


# ============================================================
# RepliedTracker 测试
# ============================================================

class TestRepliedTracker:

    def test_mark_and_check(self, tmp_path):
        """标记和检查已回复"""
        tracker = RepliedTracker(tmp_path / "replied.json")
        assert tracker.is_replied(1001) is False
        tracker.mark_replied(1001)
        assert tracker.is_replied(1001) is True
        assert tracker.is_replied(1002) is False

    def test_persistence(self, tmp_path):
        """保存和重新加载"""
        path = tmp_path / "replied.json"
        tracker1 = RepliedTracker(path)
        tracker1.mark_replied(2001)
        tracker1.mark_replied(2002)
        tracker1.save()

        # 重新加载
        tracker2 = RepliedTracker(path)
        assert tracker2.is_replied(2001) is True
        assert tracker2.is_replied(2002) is True
        assert tracker2.is_replied(2003) is False

    def test_len(self, tmp_path):
        tracker = RepliedTracker(tmp_path / "replied.json")
        assert len(tracker) == 0
        tracker.mark_replied(1)
        tracker.mark_replied(2)
        assert len(tracker) == 2

    def test_corrupted_file_recovery(self, tmp_path):
        """文件损坏时自动恢复"""
        path = tmp_path / "replied.json"
        path.write_text("not json!", encoding="utf-8")
        tracker = RepliedTracker(path)
        assert len(tracker) == 0  # 不崩溃，重新初始化

    def test_string_rpid(self, tmp_path):
        """支持字符串 rpid"""
        tracker = RepliedTracker(tmp_path / "replied.json")
        tracker.mark_replied("12345")
        assert tracker.is_replied("12345") is True
        assert tracker.is_replied(12345) is True  # int 也能匹配


# ============================================================
# MonitoredVideos 测试
# ============================================================

class TestMonitoredVideos:

    def test_add_and_list(self, tmp_path):
        """添加和列出视频"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1test1", title="测试视频1")
        mv.add_video("BV1test2", title="测试视频2", priority=1)
        assert len(mv) == 2

    def test_add_dedup(self, tmp_path):
        """重复添加时去重"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1test1", title="旧标题")
        mv.add_video("BV1test1", title="新标题", priority=2)
        assert len(mv) == 1
        videos = mv.get_sorted_videos()
        assert videos[0]["title"] == "新标题"
        assert videos[0]["priority"] == 2

    def test_priority_sort(self, tmp_path):
        """按优先级排序"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1low", priority=0)
        mv.add_video("BV1high", priority=2)
        mv.add_video("BV1mid", priority=1)

        sorted_v = mv.get_sorted_videos()
        assert sorted_v[0]["bvid"] == "BV1high"
        assert sorted_v[1]["bvid"] == "BV1mid"
        assert sorted_v[2]["bvid"] == "BV1low"

    def test_remove(self, tmp_path):
        """移除视频"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1test1")
        mv.add_video("BV1test2")
        mv.remove_video("BV1test1")
        assert len(mv) == 1
        assert mv.get_sorted_videos()[0]["bvid"] == "BV1test2"

    def test_persistence(self, tmp_path):
        """持久化"""
        path = tmp_path / "videos.json"
        mv1 = MonitoredVideos(path)
        mv1.add_video("BV1persist", title="持久化测试")
        mv1.save()

        mv2 = MonitoredVideos(path)
        assert len(mv2) == 1
        assert mv2.get_sorted_videos()[0]["bvid"] == "BV1persist"

    def test_empty_file(self, tmp_path):
        """空文件不崩溃"""
        mv = MonitoredVideos(tmp_path / "nope.json")
        assert len(mv) == 0


# ============================================================
# RateLimiter 测试
# ============================================================

class TestRateLimiter:

    def test_within_limit(self):
        rl = RateLimiter(max_per_hour=5)
        for _ in range(5):
            assert rl.can_reply() is True
            rl.record_reply()
        assert rl.can_reply() is False

    def test_remaining_count(self):
        rl = RateLimiter(max_per_hour=10)
        assert rl.remaining == 10
        rl.record_reply()
        rl.record_reply()
        assert rl.remaining == 8

    def test_old_entries_expire(self):
        """超过1小时的记录自动过期"""
        rl = RateLimiter(max_per_hour=2)
        # 模拟1小时前的记录
        rl._timestamps = [time.time() - 3700, time.time() - 3700]
        assert rl.can_reply() is True
        assert rl.remaining == 2


# ============================================================
# 评论过滤测试
# ============================================================

class TestFilterComments:

    def test_filters_replied(self, tmp_path):
        """过滤已回复"""
        tracker = RepliedTracker(tmp_path / "r.json")
        tracker.mark_replied(1001)
        replies = [_make_reply(1001), _make_reply(1002)]
        result = filter_comments(replies, tracker, "999")
        assert len(result) == 1
        assert result[0]["rpid"] == 1002

    def test_filters_self(self, tmp_path):
        """过滤自己的评论"""
        tracker = RepliedTracker(tmp_path / "r.json")
        replies = [_make_reply(1001, mid=12345), _make_reply(1002, mid=100)]
        result = filter_comments(replies, tracker, "12345")
        assert len(result) == 1
        assert result[0]["rpid"] == 1002

    def test_filters_empty(self, tmp_path):
        """过滤空评论"""
        tracker = RepliedTracker(tmp_path / "r.json")
        replies = [_make_reply(1001, msg=""), _make_reply(1002, msg="有内容")]
        result = filter_comments(replies, tracker, "999")
        assert len(result) == 1

    def test_all_pass(self, tmp_path):
        """全部通过"""
        tracker = RepliedTracker(tmp_path / "r.json")
        replies = [_make_reply(1001, msg="好治愈", mid=100)]
        result = filter_comments(replies, tracker, "999")
        assert len(result) == 1


# ============================================================
# 评论排序测试
# ============================================================

class TestPrioritizeComments:

    def test_at_mention_highest(self):
        """@ UP主 的评论排最前"""
        replies = [
            _make_reply(1, msg="普通评论", like=0),
            _make_reply(2, msg="@阿树__atree 请问这个在哪", like=0),
            _make_reply(3, msg="高赞评论", like=20),
        ]
        result = prioritize_comments(replies)
        assert result[0]["rpid"] == 2

    def test_high_like_second(self):
        """高赞评论次之"""
        replies = [
            _make_reply(1, msg="普通", like=0),
            _make_reply(2, msg="高赞", like=15),
        ]
        result = prioritize_comments(replies)
        assert result[0]["rpid"] == 2

    def test_video_priority_boost(self):
        """视频优先级加成"""
        replies = [
            _make_reply(1, msg="普通", like=0),
        ]
        # 高优先级视频
        result = prioritize_comments(replies, video_priority=2)
        assert len(result) == 1  # 不崩溃


# ============================================================
# process_video 测试（Mock API）
# ============================================================

class TestProcessVideo:

    @patch("receptionist.api.safe_send_reply")
    @patch("receptionist.api.safe_fetch_comments")
    @patch("receptionist.api.bvid_to_aid")
    @patch("receptionist.api.get_video_info")
    @patch("receptionist.time.sleep")
    def test_process_one_comment(self, mock_sleep, mock_info, mock_aid,
                                  mock_fetch, mock_send, tmp_path):
        """处理一条评论的完整流程"""
        mock_aid.return_value = 12345
        mock_info.return_value = {"title": "测试视频", "aid": 12345, "desc": "", "owner": "", "pubdate": 0}
        mock_fetch.return_value = {
            "replies": [_make_reply(9001, msg="好治愈的视频")],
            "cursor": {"next": 0, "all_count": 1},
        }
        mock_send.return_value = {"rpid": 9999, "rpid_str": "9999"}

        tracker = RepliedTracker(tmp_path / "r.json")
        rl = RateLimiter(30)

        count = process_video("BV1test", tracker, rl, video_title="测试视频")
        assert count == 1
        assert tracker.is_replied(9001) is True
        mock_send.assert_called_once()

    @patch("receptionist.api.safe_fetch_comments")
    @patch("receptionist.api.bvid_to_aid")
    @patch("receptionist.api.get_video_info")
    @patch("receptionist.time.sleep")
    def test_skip_quiet_hours(self, mock_sleep, mock_info, mock_aid,
                               mock_fetch, tmp_path):
        """静默时段跳过"""
        cfg._config_cache["receptionist"]["quiet_hours"] = [0, 23]  # 几乎全天静默
        tracker = RepliedTracker(tmp_path / "r.json")
        rl = RateLimiter(30)
        count = process_video("BV1test", tracker, rl)
        assert count == 0
        mock_fetch.assert_not_called()

    @patch("receptionist.api.safe_send_reply")
    @patch("receptionist.api.safe_fetch_comments")
    @patch("receptionist.api.bvid_to_aid")
    @patch("receptionist.api.get_video_info")
    @patch("receptionist.time.sleep")
    def test_rate_limit_stops(self, mock_sleep, mock_info, mock_aid,
                               mock_fetch, mock_send, tmp_path):
        """频率限制时停止"""
        mock_aid.return_value = 12345
        mock_info.return_value = {"title": "", "aid": 12345, "desc": "", "owner": "", "pubdate": 0}
        mock_fetch.return_value = {
            "replies": [_make_reply(i, msg=f"评论{i}") for i in range(9001, 9010)],
            "cursor": {"next": 0, "all_count": 9},
        }
        mock_send.return_value = {"rpid": 9999, "rpid_str": "9999"}

        tracker = RepliedTracker(tmp_path / "r.json")
        rl = RateLimiter(2)  # 只允许2条

        count = process_video("BV1test", tracker, rl, video_title="test")
        assert count == 2
        assert mock_send.call_count == 2


# ============================================================
# run_once 测试
# ============================================================

class TestRunOnce:

    @patch("receptionist.process_video")
    def test_run_once_calls_process(self, mock_process, tmp_path):
        """run_once 对每个视频调用 process_video"""
        mock_process.return_value = 1

        videos_path = tmp_path / "videos.json"
        mv = MonitoredVideos(videos_path)
        mv.add_video("BV1a")
        mv.add_video("BV1b")

        cfg.REPLIED_RPID_FILE = tmp_path / "replied.json"

        result = run_once(
            tracker=RepliedTracker(tmp_path / "replied.json"),
            videos=mv,
        )
        assert result["videos_processed"] == 2
        assert result["total_replies"] == 2
        assert mock_process.call_count == 2

    @patch("receptionist.process_video")
    def test_run_once_empty_videos(self, mock_process, tmp_path):
        """空监控列表不崩溃"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        cfg.REPLIED_RPID_FILE = tmp_path / "replied.json"

        result = run_once(
            tracker=RepliedTracker(tmp_path / "replied.json"),
            videos=mv,
        )
        assert result["videos_processed"] == 0
        assert result["total_replies"] == 0
        mock_process.assert_not_called()
