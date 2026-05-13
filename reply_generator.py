"""
荒野小爪回复生成器
根据评论内容、情感、上下文，生成温暖治愈的回复文本。
所有回复遵循「荒野小爪」人格设定：温暖亲切、轻柔自然、带治愈系诗意。
"""

import random
import re
import logging
from datetime import datetime

logger = logging.getLogger("receptionist.generator")

# ============================================================
# 荒野小爪 人格素材库
# ============================================================

# 治愈系表情（适量使用）
EMOJIS = ["🌲", "🌿", "✨", "🏠", "🌳", "🍃", "🌸", "☀️", "🌙", "💫", "🪵", "🔥"]

# 感谢开头变体
THANKS_OPENINGS = [
    "谢谢你的观看和留言呀～",
    "感谢你来看视频～",
    "谢谢你的支持和留言～",
    "很开心你来啦～",
    "谢谢你看到这里～",
    "感谢你的温暖留言～",
    "谢谢你喜欢～",
    "你的留言让我好开心～",
]

# 共情句式（按情感分类）
EMPATHY_PHRASES = {
    "relax": [
        "能让你放松下来真的太好了",
        "看你这么享受，我也觉得好治愈",
        "能帮到你放松心情，特别开心",
        "忙碌之后能有这样的时光，真的很棒呢",
    ],
    "healing": [
        "能治愈到你，是我最开心的事",
        "看到你说被治愈了，心里暖暖的",
        "希望这份温暖能一直陪着你",
        "能给你带来一点温暖，真的太好了",
    ],
    "repeat": [
        "看三遍都不够呢，这种感觉我懂",
        "好片值得反复回味",
        "每次看都有新的感受对吧",
        "能让人反复看的视频，一定有它的魔力",
    ],
    "sleep": [
        "当睡前陪伴真的很合适呢",
        "祝你今晚好梦～",
        "伴着这样的声音入睡，一定很安稳",
        "睡前看看治愈视频，睡眠质量都会变好呢",
    ],
    "nature": [
        "大自然真的有神奇的治愈力量",
        "森林和荒野，永远是心灵最好的归处",
        "远离喧嚣的感觉，真的很珍贵",
        "在自然面前，一切烦恼都会变小呢",
    ],
    "generic": [
        "你的感受我都懂",
        "有共鸣呢",
        "你说的太对了",
        "能理解你的心情",
    ],
}

# 尾句变体
CLOSINGS = [
    "下次再来坐坐呀～",
    "有空常来逛逛～",
    "期待下次在评论区遇见你～",
    "祝你今天也开心～",
    "希望你一切都好～",
    "下次见啦～",
    "随时来聊天呀～",
    "愿你每天都被温柔以待～",
]

# 轻幽默变体（偶尔使用）
LIGHT_HUMOR = [
    "记得喝水休息眼睛哦",
    "看太久记得站起来伸个懒腰～",
    "别看入迷忘了吃饭呀",
    "温馨提示：该活动活动啦",
]

# 特殊评论的安全回复
SAFE_REPLY = "谢谢你的留言，祝你一切都好～🌲"

# 敏感词列表（触发安全回复）
SENSITIVE_PATTERNS = [
    r"政治|政府|国家领导",
    r"骂|操|草(?!莓|原|地)|傻[逼比比]|煞笔|sb|nmsl",
    r"色情|约炮|裸聊|性",
    r"赌博|博彩|彩票|赢钱",
    r"加.{0,2}微信|加.{0,2}QQ|私聊|代运营|赚钱",
    r"死|杀|暴力|血腥",
    r"广告|推广|链接|优惠券",
]


# ============================================================
# 情感分析
# ============================================================

def detect_emotion(text: str) -> str:
    """
    分析评论情感/意图，返回情感类别。
    Args:
        text: 评论文本
    Returns:
        str: 情感类别 (relax/healing/repeat/sleep/nature/generic)
    """
    text_lower = text.lower()

    # 放松相关
    if re.search(r"放松|解压|舒服|舒缓|平静|安静|宁静|舒适|惬意|自在", text):
        return "relax"

    # 自然/户外相关（优先于治愈，因为"大自然的治愈力量"应归为 nature）
    if re.search(r"森林|荒野|户外|露营|山[里中]|溪[边流]|大自然|树[林木]|小[河溪]|自然(?!治愈)", text):
        return "nature"

    # 治愈相关
    if re.search(r"治愈|温暖|温馨|感动|暖心|舒服|美好|幸福|甜蜜|暖", text):
        return "healing"

    # 重复观看
    if re.search(r"看[了好]几遍|反复|重复|三遍|N遍|又来看|刷到|第[二三四五六七八九十]次", text):
        return "repeat"

    # 助眠相关
    if re.search(r"睡[前眠着]|助眠|入[睡着]|晚安|催眠|伴[我入]|躺[着在床上]", text):
        return "sleep"

    return "generic"


def is_sensitive(text: str) -> bool:
    """
    检测评论是否包含敏感内容。
    Args:
        text: 评论文本
    Returns:
        bool: True 表示敏感
    """
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


# ============================================================
# 回复生成
# ============================================================

def generate_reply(
    comment_message: str,
    comment_user: str = "",
    video_title: str = "",
    style: str = "warm_healing",
) -> str:
    """
    为一条评论生成荒野小爪风格的回复。
    Args:
        comment_message: 评论内容
        comment_user: 评论者用户名（可用于个性化称呼）
        video_title: 视频标题（用于上下文关联）
        style: 回复风格，默认 "warm_healing"
    Returns:
        str: 回复文本（60-130字为目标）
    """
    # 敏感内容 → 安全回复
    if is_sensitive(comment_message):
        logger.info(f"检测到敏感内容，使用安全回复: {comment_message[:30]}...")
        return SAFE_REPLY

    # 情感分析
    emotion = detect_emotion(comment_message)

    # 构建回复组件
    thanks = random.choice(THANKS_OPENINGS)
    empathy = random.choice(EMPATHY_PHRASES.get(emotion, EMPATHY_PHRASES["generic"]))
    emoji = random.choice(EMOJIS)
    closing = random.choice(CLOSINGS)

    # 偶尔加入轻幽默（20% 概率）
    humor_part = ""
    if random.random() < 0.2:
        humor_part = " " + random.choice(LIGHT_HUMOR)

    # 组装回复
    # 模板：感谢 + 共情 + [幽默] + 尾句 + 表情
    reply = f"{thanks}{empathy}～{humor_part} {closing} {emoji}"

    # 长度检查：如果太短，补充细节
    if len(reply) < 50:
        extra = _get_context_phrase(video_title, emotion)
        reply = f"{thanks}{empathy}～{extra} {closing} {emoji}"

    # 长度上限：截断到150字以内
    if len(reply) > 150:
        reply = reply[:147] + "..."

    return reply


def generate_sub_reply(
    parent_message: str,
    comment_message: str,
    comment_user: str = "",
    video_title: str = "",
) -> str:
    """
    为子评论（楼中楼）生成回复。
    子评论通常更简短、更亲切。
    Args:
        parent_message: 父评论内容
        comment_message: 子评论内容
        comment_user: 子评论用户名
        video_title: 视频标题
    Returns:
        str: 回复文本
    """
    if is_sensitive(comment_message):
        return SAFE_REPLY

    emotion = detect_emotion(comment_message)
    emoji = random.choice(EMOJIS)

    # 子评论回复更简短
    thanks = random.choice([
        "谢谢你的回复～",
        "谢谢呀～",
        "感谢你的留言～",
        "谢谢你～",
    ])
    empathy = random.choice(EMPATHY_PHRASES.get(emotion, EMPATHY_PHRASES["generic"]))
    closing = random.choice([
        "下次再聊～",
        "常来呀～",
        "有空再来～",
        "保重～",
    ])

    return f"{thanks}{empathy}～{closing} {emoji}"


def _get_context_phrase(video_title: str, emotion: str) -> str:
    """
    根据视频标题和情感生成上下文相关的短语。
    """
    if not video_title:
        return random.choice([
            "这样的视频真的很适合放松的时候看",
            "每次看都有不同的感受",
            "荒野的魅力就在于此",
        ])

    # 从标题提取关键词
    title_lower = video_title.lower()
    if "森林" in title_lower or "丛林" in title_lower:
        return "森林里的每一帧都像一幅画"
    if "木屋" in title_lower or "小屋" in title_lower or "cabin" in title_lower:
        return "看着小屋一点点建起来，特别有成就感"
    if "雨" in title_lower or "rain" in title_lower:
        return "雨声配上建造过程，简直是白噪音天花板"
    if "溪" in title_lower or "河" in title_lower or "stream" in title_lower:
        return "溪水声真的太治愈了"
    if "庇护所" in title_lower or "shelter" in title_lower:
        return "从零到有搭建庇护所，看多少遍都不腻"

    return "这样的视频真的很适合放松的时候看"


# ============================================================
# 回复去重（内容层面）
# ============================================================

_recent_replies: list[str] = []
MAX_RECENT_REPLIES = 50


def check_reply_unique(reply: str) -> bool:
    """
    检查回复内容是否与最近的回复重复。
    Args:
        reply: 待检查的回复文本
    Returns:
        bool: True 表示不重复（可用），False 表示重复
    """
    # 简单相似度：去除表情和标点后比较
    normalized = re.sub(r"[～~！!？?。，,\s🌲🌿✨🏠🌳🍃🌸☀️🌙💫🪵🔥]", "", reply)
    for recent in _recent_replies:
        recent_normalized = re.sub(r"[～~！!？?。，,\s🌲🌿✨🏠🌳🍃🌸☀️🌙💫🪵🔥]", "", recent)
        if normalized == recent_normalized:
            return False
        # 高相似度（超过80%相同）也视为重复
        if len(normalized) > 0 and len(recent_normalized) > 0:
            overlap = len(set(normalized) & set(recent_normalized))
            if overlap / max(len(set(normalized)), len(set(recent_normalized))) > 0.85:
                return False
    return True


def record_reply(reply: str):
    """记录已发送的回复，用于去重"""
    global _recent_replies
    _recent_replies.append(reply)
    if len(_recent_replies) > MAX_RECENT_REPLIES:
        _recent_replies = _recent_replies[-MAX_RECENT_REPLIES:]


def reset_recent_replies():
    """清除最近回复记录（测试用）"""
    global _recent_replies
    _recent_replies = []


# ============================================================
# 高级生成（带去重 + 重试）
# ============================================================

def generate_unique_reply(
    comment_message: str,
    comment_user: str = "",
    video_title: str = "",
    max_attempts: int = 5,
) -> str | None:
    """
    生成不重复的回复。
    Args:
        comment_message: 评论内容
        comment_user: 用户名
        video_title: 视频标题
        max_attempts: 最大尝试次数
    Returns:
        str: 不重复的回复文本，失败返回 None
    """
    for _ in range(max_attempts):
        reply = generate_reply(comment_message, comment_user, video_title)
        if check_reply_unique(reply):
            record_reply(reply)
            return reply

    # 所有尝试都重复，强制加随机后缀
    reply = generate_reply(comment_message, comment_user, video_title)
    suffix = random.choice(["✨", "🌲", "🌿", "💫"])
    reply = reply.rstrip() + f" {suffix}"
    record_reply(reply)
    return reply
