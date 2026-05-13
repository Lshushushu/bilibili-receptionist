"""
monitored_videos.json 机制 + 轻集成回归测试
覆盖：JSON 读写、批量导入、导出、与搬运程序的集成接口格式。
"""

import json
import pytest
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as cfg
from receptionist import MonitoredVideos


@pytest.fixture(autouse=True)
def reset_config():
    cfg._config_cache = {
        "cookies": {"SESSDATA": "x", "bili_jct": "x", "DedeUserID": "1"},
    }
    yield
    cfg._config_cache = None


# ============================================================
# JSON 文件格式测试
# ============================================================

class TestMonitoredVideosJSON:

    def test_file_schema(self, tmp_path):
        """monitored_videos.json 符合预期 schema"""
        path = tmp_path / "monitored_videos.json"
        mv = MonitoredVideos(path)
        mv.add_video("BV1test1", title="测试视频", priority=1)
        mv.save()

        # 读取原始 JSON 验证结构
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "videos" in raw
        assert "updated_at" in raw
        assert isinstance(raw["videos"], list)

        video = raw["videos"][0]
        assert "bvid" in video
        assert "title" in video
        assert "priority" in video
        assert "added_at" in video

    def test_empty_file_creates_valid_json(self, tmp_path):
        """空列表保存后文件格式正确"""
        path = tmp_path / "empty.json"
        mv = MonitoredVideos(path)
        mv.save()

        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["videos"] == []
        assert "updated_at" in raw

    def test_load_existing_json(self, tmp_path):
        """能加载已存在的 JSON 文件"""
        path = tmp_path / "existing.json"
        data = {
            "videos": [
                {"bvid": "BV1exist1", "title": "已有视频", "priority": 2, "added_at": "2026-05-10T10:00:00"},
                {"bvid": "BV1exist2", "title": "另一个", "priority": 0, "added_at": "2026-05-09T08:00:00"},
            ],
            "updated_at": "2026-05-10T10:00:00",
        }
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        mv = MonitoredVideos(path)
        assert len(mv) == 2
        sorted_v = mv.get_sorted_videos()
        assert sorted_v[0]["bvid"] == "BV1exist1"  # priority=2 排前面
        assert sorted_v[1]["bvid"] == "BV1exist2"


# ============================================================
# 批量导入格式测试
# ============================================================

class TestBatchImport:

    def test_import_simple_list(self, tmp_path):
        """导入纯 BV 号列表"""
        import_file = tmp_path / "import.json"
        import_file.write_text(json.dumps(["BV1aaa", "BV1bbb", "BV1ccc"]), encoding="utf-8")

        data = json.loads(import_file.read_text(encoding="utf-8"))
        mv = MonitoredVideos(tmp_path / "videos.json")

        for bvid in data:
            mv.add_video(bvid)

        assert len(mv) == 3

    def test_import_object_list(self, tmp_path):
        """导入带优先级的对象列表"""
        import_file = tmp_path / "import.json"
        items = [
            {"bvid": "BV1new1", "priority": 2},
            {"bvid": "BV1new2", "priority": 1},
            {"bvid": "BV1new3"},
        ]
        import_file.write_text(json.dumps(items), encoding="utf-8")

        data = json.loads(import_file.read_text(encoding="utf-8"))
        mv = MonitoredVideos(tmp_path / "videos.json")

        for item in data:
            bvid = item if isinstance(item, str) else item.get("bvid", "")
            priority = 0 if isinstance(item, str) else item.get("priority", 0)
            if bvid:
                mv.add_video(bvid, priority=priority)

        assert len(mv) == 3
        sorted_v = mv.get_sorted_videos()
        assert sorted_v[0]["bvid"] == "BV1new1"  # priority=2
        assert sorted_v[0]["priority"] == 2

    def test_import_wrapped_format(self, tmp_path):
        """导入 {\"bvids\": [...]} 包装格式"""
        import_file = tmp_path / "import.json"
        data = {
            "bvids": [
                {"bvid": "BV1wrap1", "priority": 1},
                "BV1wrap2",
            ]
        }
        import_file.write_text(json.dumps(data), encoding="utf-8")

        raw = json.loads(import_file.read_text(encoding="utf-8"))
        items = raw.get("bvids") or raw.get("videos") or []
        mv = MonitoredVideos(tmp_path / "videos.json")

        for item in items:
            bvid = item if isinstance(item, str) else item.get("bvid", "")
            priority = 0 if isinstance(item, str) else item.get("priority", 0)
            if bvid:
                mv.add_video(bvid, priority=priority)

        assert len(mv) == 2

    def test_import_videos_wrapped_format(self, tmp_path):
        """导入 {\"videos\": [...]} 包装格式"""
        import_file = tmp_path / "import.json"
        data = {"videos": ["BV1v1", "BV1v2"]}
        import_file.write_text(json.dumps(data), encoding="utf-8")

        raw = json.loads(import_file.read_text(encoding="utf-8"))
        items = raw.get("bvids") or raw.get("videos") or []
        mv = MonitoredVideos(tmp_path / "videos.json")

        for item in items:
            bvid = item if isinstance(item, str) else item.get("bvid", "")
            if bvid:
                mv.add_video(bvid)

        assert len(mv) == 2

    def test_import_invalid_json(self, tmp_path):
        """无效 JSON 时文件损坏不崩溃"""
        import_file = tmp_path / "bad.json"
        import_file.write_text("{broken", encoding="utf-8")

        with pytest.raises(json.JSONDecodeError):
            json.loads(import_file.read_text(encoding="utf-8"))

    def test_import_empty_list(self, tmp_path):
        """空列表导入不报错"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        items = []
        for item in items:
            mv.add_video(item)
        assert len(mv) == 0


# ============================================================
# 搬运程序集成接口测试
# ============================================================

class TestUploaderIntegration:
    """
    模拟搬运程序写入 BV 号的场景。
    搬运程序完成上传后，通过写 JSON 文件通知 receptionist。
    """

    def test_uploader_writes_bvid(self, tmp_path):
        """搬运程序写入 BV 号，receptionist 能读取"""
        shared_file = tmp_path / "monitored_videos.json"

        # 模拟搬运程序写入
        uploader_data = {
            "videos": [
                {"bvid": "BV1uploaded1", "title": "新建造视频", "priority": 1, "added_at": datetime.now().isoformat()},
            ],
            "updated_at": datetime.now().isoformat(),
        }
        shared_file.write_text(json.dumps(uploader_data, ensure_ascii=False), encoding="utf-8")

        # receptionist 读取
        mv = MonitoredVideos(shared_file)
        assert len(mv) == 1
        assert mv.get_sorted_videos()[0]["bvid"] == "BV1uploaded1"
        assert mv.get_sorted_videos()[0]["priority"] == 1

    def test_uploader_appends_bvid(self, tmp_path):
        """搬运程序追加 BV 号，不覆盖已有数据"""
        shared_file = tmp_path / "monitored_videos.json"

        # receptionist 先添加了一些视频
        mv = MonitoredVideos(shared_file)
        mv.add_video("BV1manual", title="手动添加", priority=0)

        # 模拟搬运程序追加（读取 → 追加 → 写回）
        existing = json.loads(shared_file.read_text(encoding="utf-8"))
        existing["videos"].append({
            "bvid": "BV1uploaded2",
            "title": "搬运程序新增",
            "priority": 1,
            "added_at": datetime.now().isoformat(),
        })
        existing["updated_at"] = datetime.now().isoformat()
        shared_file.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        # receptionist 重新加载
        mv2 = MonitoredVideos(shared_file)
        assert len(mv2) == 2
        bvids = [v["bvid"] for v in mv2.get_sorted_videos()]
        assert "BV1manual" in bvids
        assert "BV1uploaded2" in bvids

    def test_export_format_is_importable(self, tmp_path):
        """导出的格式可以被重新导入"""
        path1 = tmp_path / "export.json"
        mv1 = MonitoredVideos(path1)
        mv1.add_video("BV1export1", title="导出测试", priority=2)
        mv1.save()

        # 读取导出数据
        exported = json.loads(path1.read_text(encoding="utf-8"))

        # 用导出数据创建新的 MonitoredVideos
        path2 = tmp_path / "imported.json"
        mv2 = MonitoredVideos(path2)
        for v in exported["videos"]:
            mv2.add_video(v["bvid"], title=v.get("title", ""), priority=v.get("priority", 0))

        assert len(mv2) == 1
        assert mv2.get_sorted_videos()[0]["bvid"] == "BV1export1"
        assert mv2.get_sorted_videos()[0]["priority"] == 2


# ============================================================
# 优先级 + 新视频重点接待测试
# ============================================================

class TestPriorityHandling:

    def test_new_video_high_priority(self, tmp_path):
        """新上传视频设为高优先级"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1new", title="刚搬运完的新视频", priority=2)
        mv.add_video("BV1old", title="老视频", priority=0)

        sorted_v = mv.get_sorted_videos()
        assert sorted_v[0]["bvid"] == "BV1new"
        assert sorted_v[0]["priority"] == 2

    def test_priority_upgrade(self, tmp_path):
        """重复添加时优先级取最大值"""
        mv = MonitoredVideos(tmp_path / "videos.json")
        mv.add_video("BV1test", priority=0)
        mv.add_video("BV1test", priority=2)  # 升级

        assert mv.get_sorted_videos()[0]["priority"] == 2
        assert len(mv) == 1  # 不重复
