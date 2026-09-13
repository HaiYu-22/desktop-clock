@echo off
chcp 65001 >nul
rem ============================================
rem  桌面时钟 打包脚本
rem  产物：dist\桌面时钟.exe（单文件，双击即用）
rem ============================================
cd /d "%~dp0"

rem ---- 查找 Python ----
set "PY="
for /f "delims=" %%i in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PY=%%i"
if not defined PY for /f "delims=" %%i in ('where python 2^>nul') do if not defined PY set "PY=%%i"
if not defined PY (
  echo [错误] 没有找到 Python，请先安装 Python 3。
  pause
  exit /b 1
)

rem ---- 补齐依赖 ----
"%PY%" -c "import PIL" >nul 2>nul
if errorlevel 1 (
  echo 正在安装 Pillow...
  "%PY%" -m pip install pillow
  if errorlevel 1 goto :failed
)
"%PY%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo 正在安装 PyInstaller...
  "%PY%" -m pip install pyinstaller
  if errorlevel 1 goto :failed
)

rem ---- 打包 ----
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name 桌面时钟 desktop_clock.py
if errorlevel 1 goto :failed

echo.
echo 打包完成：dist\桌面时钟.exe
pause
exit /b 0

:failed
echo.
echo [错误] 打包失败，请查看上面的输出。
pause
exit /b 1