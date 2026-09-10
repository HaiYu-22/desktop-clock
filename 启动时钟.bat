@echo off
chcp 65001 >nul
rem ============================================
rem  桌面时钟 启动脚本（自动查找 Python）
rem  首次运行会自动安装 Pillow 依赖
rem  开机自启动请在时钟的右键菜单里勾选
rem ============================================
cd /d "%~dp0"

rem ---- 自动查找 Python：py 启动器 -> PATH -> 常见安装目录 ----
set "PYW="
set "PY="

rem 1) py 启动器（能自动选到最新的 Python 3）
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if defined PY set "PYW=%PY:python.exe=pythonw.exe%"

rem 2) PATH 里的 pythonw
if not defined PYW for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"

rem 3) 用户目录 / Program Files 下的 Python3*（版本号大的优先）
if not defined PYW for /f "delims=" %%d in ('dir /b /o-n "%LOCALAPPDATA%\Programs\Python\Python3*" 2^>nul') do if not defined PYW if exist "%LOCALAPPDATA%\Programs\Python\%%d\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\%%d\pythonw.exe"
if not defined PYW for /f "delims=" %%d in ('dir /b /o-n "C:\Program Files\Python3*" 2^>nul') do if not defined PYW if exist "C:\Program Files\%%d\pythonw.exe" set "PYW=C:\Program Files\%%d\pythonw.exe"

if not defined PYW (
  echo [错误] 没有找到 Python，请先安装 Python 3。
  echo        安装时勾选 "Add python.exe to PATH"，或到 python.org 下载安装。
  pause
  exit /b 1
)

rem ---- 用同目录的 python.exe 检查并安装 Pillow ----
set "PY=%PYW:pythonw.exe=python.exe%"
if not exist "%PY%" set "PY=%PYW%"

"%PY%" -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 正在安装 Pillow（首次运行需要联网）...
  "%PY%" -m pip install pillow
  if errorlevel 1 (
    echo [错误] Pillow 安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

start "" "%PYW%" "%~dp0desktop_clock.py"
exit /b 0