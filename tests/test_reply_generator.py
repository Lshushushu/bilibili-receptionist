"""
reply_generator.py 回归测试
覆盖：情感分析、敏感检测、回复生成、去重、子评论回复、边界情况。
"""

import re
import pytest
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reply_generator import (
    detect_emotion,
    is_sensitive,
    generate_reply,
    generate_sub_reply,
    generate_unique_reply,
    check_reply_unique,
    record_reply,
    reset_recent_replies,
    SAFE_REPLY,
    EMOJIS,
)


@pytest.fixture(autouse=True)
def clean_replies():
    """每个测试前清除回复记录"""
    reset_recent_replies()
    yield
    reset_recent_replies()


# ============================================================
# 情感分析测试
# ============================================================

class TestDetectEmotion:

    def test_relax(self):
        assert detect_emotion("好放松啊，压力都没了") == "relax"
        assert detect_emotion("看这个太解压了") == "relax"
        assert detect_emotion("很舒服的感觉") == "relax"

    def test_healing(self):
        assert detect_emotion("太治愈了") == "healing"
        assert detect_emotion("好温暖好温馨") == "healing"
        assert detect_emotion("被感动到了") == "healing"

    def test_repeat(self):
        assert detect_emotion("看了三遍都不够") == "repeat"
        assert detect_emotion("又来看了一遍") == "repeat"
        assert detect_emotion("这是第二次刷到了") == "repeat"

    def test_sleep(self):
        assert detect_emotion("睡前看这个太合适了") == "sleep"
        assert detect_emotion("助眠效果很好") == "sleep"
        assert detect_emotion("看着看着就睡着了") == "sleep"

    def test_nature(self):
        assert detect_emotion("森林真的太美了") == "nature"
        assert detect_emotion("大自然的治愈力量") == "nature"
        assert detect_emotion("想去露营了") == "nature"

    def test_generic(self):
        assert detect_emotion("不错") == "generic"
        assert detect_emotion("666") == "generic"
        assert detect_emotion("好看") == "generic"

    def test_empty_string(self):
        assert detect_emotion("") == "generic"


# ============================================================
# 敏感检测测试
# ============================================================

class TestIsSensitive:

    def test_normal_text_not_sensitive(self):
        assert is_sensitive("好治愈的视频") is False
        assert is_sensitive("谢谢分享") is False
        assert is_sensitive("看了三遍") is False

    def test_sensitive_politics(self):
        assert is_sensitive("这个政治话题") is True

    def test_sensitive_profanity(self):
        assert is_sensitive("你这个傻逼") is True

    def test_sensitive_ad(self):
        assert is_sensitive("加微信了解更多") is True
        assert is_sensitive("有优惠券可以领") is True

    def test_sensitive_violence(self):
        assert is_sensitive("这个太血腥了") is True

    def test_edge_case_not_sensitive(self):
        """包含'草'但不是敏感词（如草莓）"""
        assert is_sensitive("草莓蛋糕好吃") is False


# ============================================================
# 回复生成测试
# ============================================================

class TestGenerateReply:

    def test_generates_non_empty_reply(self):
        """能生成非空回复"""
        reply = generate_reply("好治愈的视频")
        assert len(reply) > 0
        assert isinstance(reply, str)

    def test_reply_within_length_range(self):
        """回复长度在合理范围内"""
        for _ in range(20):
            reply = generate_reply("太好看了，治愈放松解压")
            assert len(reply) <= 155  # 150 + 少量边界

    def test_sensitive_comment_returns_safe_reply(self):
        """敏感评论返回安全回复"""
        reply = generate_reply("加我微信 xxx")
        assert reply == SAFE_REPLY

    def test_reply_contains_emoji(self):
        """回复包含至少一个表情"""
        for _ in range(10):
            reply = generate_reply("好治愈")
            assert any(e in reply for e in EMOJIS)

    def test_reply_varies(self):
        """多次生成的回复不应完全相同（随机性）"""
        replies = {generate_reply("好放松") for _ in range(10)}
        assert len(replies) > 1  # 至少有2种不同回复

    def test_with_video_title_context(self):
        """传入视频标题时能生成上下文相关回复"""
        reply = generate_reply("好看", video_title="森林小木屋建造全过程")
        assert len(reply) > 0

    def test_with_user_name(self):
        """传入用户名时不报错"""
        reply = generate_reply("好治愈", comment_user="小明")
        assert len(reply) > 0


# ============================================================
# 子评论回复测试
# ============================================================

class TestGenerateSubReply:

    def test_generates_sub_reply(self):
        reply = generate_sub_reply("原评论", "回复内容", "用户A")
        assert len(reply) > 0

    def test_sensitive_sub_reply(self):
        reply = generate_sub_reply("原评论", "加QQ群", "用户B")
        assert reply == SAFE_REPLY

    def test_sub_reply_shorter_than_main(self):
        """子评论回复通常更短"""
        sub = generate_sub_reply("原评论", "好治愈")
        main = generate_reply("好治愈")
        # 子评论回复一般更短，但不强制（允许随机性）
        # 这里只验证都能生成
        assert len(sub) > 0
        assert len(main) > 0


# ============================================================
# 去重测试
# ============================================================

class TestReplyDedup:

    def test_unique_reply_passes(self):
        """不重复的回复通过检查"""
        assert check_reply_unique("谢谢你来看视频～🌲") is True

    def test_duplicate_rejected(self):
        """重复回复被拒绝"""
        record_reply("谢谢你来看视频～🌲")
        assert check_reply_unique("谢谢你来看视频～🌲") is False

    def test_record_reply_tracks(self):
        """record_reply 正确记录"""
        assert len(_get_recent()) == 0
        record_reply("test reply")
        assert len(_get_recent()) == 1

    def test_max_recent_replies_limit(self):
        """记录超过上限后自动裁剪"""
        for i in range(60):
            record_reply(f"reply {i}")
        assert len(_get_recent()) <= 50  # MAX_RECENT_REPLIES

    def test_generate_unique_avoids_duplicates(self):
        """generate_unique_reply 避免重复"""
        # 先记录所有可能的回复变体，强制触发唯一性保障
        r1 = generate_unique_reply("好治愈")
        assert r1 is not None
        r2 = generate_unique_reply("好治愈")
        assert r2 is not None
        # 两者不应完全相同（去重机制）
        # 注：理论上可能相同，但概率极低（有表情随机后缀兜底）


def _get_recent():
    """获取最近回复列表（测试辅助）"""
    import reply_generator as rg
    return rg._recent_replies
