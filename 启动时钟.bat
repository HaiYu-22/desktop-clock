@echo off
rem ============================================
rem  桌面时钟 启动脚本（Python 版）
rem  首次运行会自动检测并安装 Pillow 依赖
rem  已运行则直接启动，无窗口闪烁
rem ============================================
cd /d "%~dp0"

rem 确定 pythonw（系统 PATH 或用户安装目录）
set "PYW="
where pythonw >nul 2>nul && set "PYW=pythonw"
if not defined PYW (
  if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
)
if not defined PYW (
  echo [错误] 未找到 pythonw，请先安装 Python。
  pause
  exit /b 1
)

rem 检查 Pillow 是否可用（需要 python.exe 运行检测）
set "PY=%PYW:pythonw=python%"
if not exist "%PY%" set "PY=python"
%PY% -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 正在安装 Pillow（首次运行需要联网）...
  %PY% -m pip install pillow
  if errorlevel 1 (
    echo [错误] Pillow 安装失败，请检查网络。
    pause
    exit /b 1
  )
)

start "" "%PYW%" desktop_clock.py
exit
