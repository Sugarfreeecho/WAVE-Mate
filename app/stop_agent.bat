@echo off
chcp 65001 >nul
title Stop Agent

echo.  ⏹️  正在关闭 Agent（通过端口 8192）...
echo.

:: 查找监听 8192 端口的进程并终止
set "PID="
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8192"') do (
    set "PID=%%a"
)

if not "%PID%"=="" (
    taskkill /f /pid %PID% >nul 2>&1
    if errorlevel 1 (
        echo.  ❌ 关闭失败（可能是权限问题，请以管理员身份运行）
    ) else (
        echo.  ✅ Agent 已成功关闭（PID: %PID%）
    )
) else (
    echo.  ❌ 未找到监听 8192 端口的 Agent 进程
    echo.
    echo.  提示：也可以打开任务管理器 (Ctrl+Shift+Esc)，
    echo.  在"进程"标签页中查找并手动结束 python.exe 或 GeneralAgent.exe
)

echo.
pause
