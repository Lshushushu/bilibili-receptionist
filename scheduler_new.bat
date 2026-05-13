@echo off
REM ============================================
REM B站评论接待员 - 定时任务脚本
REM 新视频高频检查（每3小时，白天时段）
REM 用法：由 Windows Task Scheduler 调用
REM ============================================
cd /d "%~dp0"
C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe run.py --once --mode new >> logs\scheduler.log 2>&1
