@echo off
REM ============================================
REM B站评论接待员 - 安装 Windows 定时任务
REM 以管理员身份运行此脚本
REM ============================================
echo 正在创建定时任务...

REM 任务1：中午全量扫描（含新视频发现）
schtasks /create /tn "BilibiliReceptionist-Noon" /tr "\"%~dp0scheduler_all.bat\"" /sc daily /st 12:00 /f
echo ✅ 已创建：全量扫描 - 中午 12:00

REM 任务2：晚上全量扫描（含新视频发现）
schtasks /create /tn "BilibiliReceptionist-Night" /tr "\"%~dp0scheduler_all.bat\"" /sc daily /st 23:00 /f
echo ✅ 已创建：全量扫描 - 晚上 23:00

echo.
echo ========================================
echo 定时任务安装完成！
echo.
echo 运行计划：
echo   全量扫描：12:00 / 23:00（含自动发现新视频）
echo   每次扫描前自动检查频道新视频
echo ========================================
echo.
echo 查看任务：schtasks /query /tn "BilibiliReceptionist*"
echo 删除任务：schtasks /delete /tn "BilibiliReceptionist*" /f
pause
