"""
config.py 回归测试
覆盖：配置加载、Cookie 提取、便捷函数、校验、路径、静默时段
"""

import json
import pytest
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch

# 项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def valid_config():
    """合法配置样本"""
    return {
        "cookies": {
            "SESSDATA": "test_sessdata",
            "bili_jct": "test_csrf",
            "DedeUserID": "12345"
        },
        "receptionist": {
            "check_interval_minutes": 10,
            "reply_delay_min": 3,
            "reply_delay_max": 15,
            "max_replies_per_hour": 30,
            "quiet_hours": [0, 7],
            "default_reply_style": "warm_healing",
            "bot_name": "荒野小爪"
        }
    }


@pytest.fixture
def minimal_config():
    """最小合法配置（只有 cookies）"""
    return {
        "cookies": {
            "SESSDATA": "test_sessdata",
            "bili_jct": "test_csrf",
            "DedeUserID": "12345"
        }
    }


@pytest.fixture
def config_file(tmp_path, valid_config):
    """创建临时配置文件"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(valid_config, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture
def minimal_config_file(tmp_path, minimal_config):
    """创建最小配置文件"""
    path = tmp_path / "config.json"
    path.write_text(json.dumps(minimal_config, ensure_ascii=False), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_cache():
    """每个测试前清除配置缓存"""
    cfg._config_cache = None
    yield
    cfg._config_cache = None


# ============================================================
# 配置加载测试
# ============================================================

class TestLoadConfig:

    def test_load_valid_config(self, config_file):
        """能正确加载合法配置"""
        result = cfg.load_config(config_file)
        assert "cookies" in result
        assert result["cookies"]["SESSDATA"] == "test_sessdata"

    def test_load_nonexistent_file(self, tmp_path):
        """配置文件不存在时抛出 FileNotFoundError"""
        with pytest.raises(FileNotFoundError, match="配置文件不存在"):
            cfg.load_config(tmp_path / "nonexistent.json")

    def test_load_invalid_json(self, tmp_path):
        """JSON 格式错误时抛出 JSONDecodeError"""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            cfg.load_config(bad_file)

    def test_config_caching(self, config_file):
        """默认缓存配置，第二次调用不重新读取文件"""
        cfg.load_config(config_file)
        # 删除文件后仍能从缓存读取
        config_file.unlink()
        result = cfg.load_config()
        assert result["cookies"]["SESSDATA"] == "test_sessdata"

    def test_reload_config(self, config_file, tmp_path):
        """reload_config 清除缓存并重新加载"""
        cfg.load_config(config_file)
        # 修改文件
        new_config = {"cookies": {"SESSDATA": "new_data", "bili_jct": "new_csrf", "DedeUserID": "999"}}
        config_file.write_text(json.dumps(new_config), encoding="utf-8")
        # patch CONFIG_FILE 指向临时文件
        with patch.object(cfg, "CONFIG_FILE", config_file):
            result = cfg.reload_config()
        assert result["cookies"]["SESSDATA"] == "new_data"


# ============================================================
# Cookie 提取测试
# ============================================================

class TestGetCookies:

    def test_get_cookies_valid(self, config_file):
        """正确提取完整 Cookie"""
        cfg.load_config(config_file)
        cookies = cfg.get_cookies()
        assert cookies["SESSDATA"] == "test_sessdata"
        assert cookies["bili_jct"] == "test_csrf"
        assert cookies["DedeUserID"] == "12345"

    def test_get_cookies_missing_field(self, tmp_path):
        """缺少必要字段时抛出 KeyError"""
        bad = {"cookies": {"SESSDATA": "x"}}
        path = tmp_path / "config.json"
        path.write_text(json.dumps(bad), encoding="utf-8")
        cfg.load_config(path)
        with pytest.raises(KeyError, match="缺少必要 Cookie 字段"):
            cfg.get_cookies()

    def test_get_cookie_string(self, config_file):
        """Cookie 字符串格式正确"""
        cfg.load_config(config_file)
        s = cfg.get_cookie_string()
        assert "SESSDATA=test_sessdata" in s
        assert "bili_jct=test_csrf" in s
        assert "DedeUserID=12345" in s
        assert "; " in s

    def test_get_csrf(self, config_file):
        """正确获取 CSRF token"""
        cfg.load_config(config_file)
        assert cfg.get_csrf() == "test_csrf"

    def test_get_uid(self, config_file):
        """正确获取 UID"""
        cfg.load_config(config_file)
        assert cfg.get_uid() == "12345"


# ============================================================
# 接待配置测试
# ============================================================

class TestReceptionistConfig:

    def test_full_receptionist_config(self, config_file):
        """完整配置正确返回"""
        cfg.load_config(config_file)
        rconf = cfg.get_receptionist_config()
        assert rconf["check_interval_minutes"] == 10
        assert rconf["reply_delay_min"] == 3
        assert rconf["reply_delay_max"] == 15
        assert rconf["max_replies_per_hour"] == 30
        assert rconf["quiet_hours"] == [0, 7]
        assert rconf["bot_name"] == "荒野小爪"

    def test_defaults_when_missing(self, minimal_config_file):
        """receptionist 配置缺失时返回默认值"""
        cfg.load_config(minimal_config_file)
        rconf = cfg.get_receptionist_config()
        assert rconf["check_interval_minutes"] == 15
        assert rconf["reply_delay_min"] == 3
        assert rconf["reply_delay_max"] == 15
        assert rconf["max_replies_per_hour"] == 30
        assert rconf["quiet_hours"] == [0, 7]


# ============================================================
# 配置校验测试
# ============================================================

class TestValidateConfig:

    def test_valid_config_passes(self, valid_config):
        """合法配置校验通过"""
        issues = cfg.validate_config(valid_config)
        assert issues == []

    def test_missing_cookies(self):
        """缺少 cookies 时返回问题列表"""
        issues = cfg.validate_config({})
        assert len(issues) == 3  # SESSDATA, bili_jct, DedeUserID

    def test_empty_cookie_value(self, valid_config):
        """Cookie 值为空时报告问题"""
        valid_config["cookies"]["SESSDATA"] = ""
        issues = cfg.validate_config(valid_config)
        assert any("Cookie 为空: SESSDATA" in i for i in issues)

    def test_invalid_delay_range(self, valid_config):
        """delay_max < delay_min 时报告问题"""
        valid_config["receptionist"]["reply_delay_min"] = 20
        valid_config["receptionist"]["reply_delay_max"] = 5
        issues = cfg.validate_config(valid_config)
        assert any("reply_delay_max" in i for i in issues)

    def test_invalid_max_replies(self, valid_config):
        """max_replies_per_hour 超范围时报告问题"""
        valid_config["receptionist"]["max_replies_per_hour"] = 200
        issues = cfg.validate_config(valid_config)
        assert any("max_replies_per_hour" in i for i in issues)

    def test_invalid_quiet_hours(self, valid_config):
        """quiet_hours 格式错误时报告问题"""
        valid_config["receptionist"]["quiet_hours"] = [0, 7, 12]
        issues = cfg.validate_config(valid_config)
        assert any("quiet_hours" in i for i in issues)

    def test_file_not_found(self, tmp_path):
        """配置文件不存在时返回错误"""
        with patch.object(cfg, "CONFIG_FILE", tmp_path / "nope.json"):
            issues = cfg.validate_config()
            assert any("配置文件不存在" in i for i in issues)


# ============================================================
# 路径测试
# ============================================================

class TestPaths:

    def test_log_path_format(self):
        """日志路径格式正确"""
        dt = datetime(2026, 5, 11)
        path = cfg.get_log_path(dt)
        assert path.name == "receptionist_2026-05-11.log"
        assert path.parent == cfg.LOG_DIR

    def test_base_dir_is_project_root(self):
        """BASE_DIR 指向项目根目录"""
        assert cfg.BASE_DIR.name == "bilibili-receptionist"

    def test_data_dir_exists(self):
        """data 目录自动创建"""
        assert cfg.DATA_DIR.exists()

    def test_log_dir_exists(self):
        """logs 目录自动创建"""
        assert cfg.LOG_DIR.exists()


# ============================================================
# 静默时段测试
# ============================================================

class TestQuietHours:

    def test_in_quiet_hours(self, valid_config):
        """凌晨 3 点在静默时段内"""
        cfg._config_cache = valid_config
        mock_now = datetime(2026, 5, 11, 3, 0)
        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: mock_now
            assert cfg.is_quiet_hours() is True

    def test_outside_quiet_hours(self, valid_config):
        """中午 12 点不在静默时段内"""
        cfg._config_cache = valid_config
        mock_now = datetime(2026, 5, 11, 12, 0)
        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: mock_now
            assert cfg.is_quiet_hours() is False

    def test_quiet_hours_boundary_start(self, valid_config):
        """起始边界（0 点）在静默时段内"""
        cfg._config_cache = valid_config
        mock_now = datetime(2026, 5, 11, 0, 0)
        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: mock_now
            assert cfg.is_quiet_hours() is True

    def test_quiet_hours_boundary_end(self, valid_config):
        """结束边界（7 点）不在静默时段内"""
        cfg._config_cache = valid_config
        mock_now = datetime(2026, 5, 11, 7, 0)
        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: mock_now
            assert cfg.is_quiet_hours() is False

    def test_cross_midnight_quiet_hours(self, valid_config):
        """跨午夜静默时段 [23, 7]"""
        valid_config["receptionist"]["quiet_hours"] = [23, 7]
        cfg._config_cache = valid_config

        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: datetime(2026, 5, 11, 23, 30)
            assert cfg.is_quiet_hours() is True

        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: datetime(2026, 5, 11, 2, 0)
            assert cfg.is_quiet_hours() is True

        with patch.object(cfg, "datetime", wraps=cfg.datetime) as mock_dt:
            mock_dt.now = lambda: datetime(2026, 5, 11, 12, 0)
            assert cfg.is_quiet_hours() is False
