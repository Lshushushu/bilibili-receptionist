# 轻集成说明：bilibili-uploader ↔ bilibili-receptionist

## 概述

两个项目**完全独立运行**，通过共享的 `monitored_videos.json` 文件进行轻量集成。

```
bilibili-uploader/                    bilibili-receptionist/
  ↓ 上传完成                           ↓ 定时检查
  写入 BV 号 → monitored_videos.json → 读取并接待评论
```

## 集成方式

### 方式一：手动添加（最简单）

```bash
# 添加单个视频
cd bilibili-receptionist
python run.py --add BV1xxxxxxxxx --priority 1

# 列出当前监控
python run.py --list
```

### 方式二：搬运程序完成后自动写入

在 `bilibili-uploader` 的上传完成回调中，追加以下代码：

```python
import json
from pathlib import Path
from datetime import datetime

def notify_receptionist(bvid: str, title: str = "", priority: int = 1):
    """通知 receptionist 新视频已上传"""
    monitor_file = Path(__file__).parent.parent / "bilibili-receptionist" / "monitored_videos.json"
    
    if monitor_file.exists():
        data = json.loads(monitor_file.read_text(encoding="utf-8"))
    else:
        data = {"videos": []}
    
    # 去重
    existing_bvids = {v["bvid"] for v in data["videos"]}
    if bvid not in existing_bvids:
        data["videos"].append({
            "bvid": bvid,
            "title": title,
            "priority": priority,  # 新上传设为 1
            "added_at": datetime.now().isoformat(),
        })
        data["updated_at"] = datetime.now().isoformat()
        monitor_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[集成] 已通知 receptionist: {bvid}")
```

### 方式三：批量导入

```bash
# 从 JSON 文件导入
python run.py --import-bvids upload_history.json

# 支持的格式：
# 纯列表: ["BV1xxx", "BV2xxx"]
# 对象列表: [{"bvid": "BV1xxx", "priority": 1}, ...]
# 包装格式: {"bvids": [...]} 或 {"videos": [...]}
```

## monitored_videos.json 格式

```json
{
  "videos": [
    {
      "bvid": "BV1xxxxxxxxx",
      "title": "森林小木屋建造全过程",
      "priority": 1,
      "added_at": "2026-05-11T15:00:00"
    }
  ],
  "updated_at": "2026-05-11T15:00:00"
}
```

### 优先级说明

| priority | 含义 | 接待策略 |
|----------|------|---------|
| 0 | 普通视频 | 常规检查频率 |
| 1 | 新上传视频 | 前 3 天重点接待，评论优先处理 |
| 2 | 高优先 | 始终优先处理 |

## 注意事项

1. **文件锁**：两个程序同时写入时可能冲突。建议搬运程序写入后等 1 秒再让 receptionist 读取。
2. **去重**：`MonitoredVideos.add_video()` 自动按 bvid 去重，重复添加不会产生多条记录。
3. **优先级升级**：重复添加时优先级取最大值（不会降级）。
4. **receptionist 自动重载**：每轮循环都会重新读取 `monitored_videos.json`，无需重启。
