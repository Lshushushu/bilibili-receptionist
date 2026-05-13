@echo off
REM ============================================
REM B站评论接待员 - 全量扫描 + 新视频发现
REM 每天2次：中午12:00 / 晚上23:00
REM 用法：由 Windows Task Scheduler 调用
REM ============================================
cd /d "%~dp0"
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe check_new_videos.py >> logs\scheduler.log 2>&1
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe run.py --once --mode all >> logs\scheduler.log 2>&1
