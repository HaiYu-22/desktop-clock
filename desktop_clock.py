# -*- coding: utf-8 -*-
"""
桌面时钟 —— 表盘式 v2
- Pillow 3x 超采样渲染 + LANCZOS 缩放，消除边缘锯齿
- Win32 UpdateLayeredWindow 实现真正的逐像素透明（无方框、无白底）
- 表盘面完全透明：只显示表圈、刻度、数字、指针，透过表盘可见桌面
- 秒针精确到秒（平滑走动，每秒准确落位）
- 表盘下半部分以数字显示 年月日 与 星期（字号小于表盘数字）
- 无边框、可拖动；默认不置顶（可被其他窗口遮挡，遮挡部分不显示，照常走时）
- 记忆窗口位置：关闭/拖动结束自动保存，下次启动恢复原位
- 开机自启动：右键菜单勾选"开机自启动"，随系统启动（注册表 HKCU\\...\\Run）

运行方式：python desktop_clock.py
操作：左键按住拖动；双击切换置顶；右键菜单（置顶 / 秒针模式 / 开机自启动 / 退出）
"""

import ctypes
import datetime
import json
import math
import os
import subprocess
import sys
import tkinter as tk
from ctypes import wintypes as wt
from PIL import Image, ImageDraw, ImageFont
from tkinter import messagebox

try:
    import winreg          # Windows 注册表（自启动用）
except ImportError:
    winreg = None

# ============ 配置 ============
SIZE = 360                 # 窗口边长（逻辑像素）
CX, CY = SIZE // 2, SIZE // 2
R = 162                    # 表盘半径
SS = 3                     # 超采样倍数（抗锯齿）

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = 'desktop_clock_config.json'   # 记忆窗口位置 / 置顶 / 秒针模式
LOG_FILE = 'desktop_clock.log'              # 自启动等关键事件日志（pythonw 无控制台）
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = '桌面时钟'
STARTUP_LNK_NAME = '桌面时钟.lnk'            # "启动"文件夹里的快捷方式（第二通道）
AUTOSTART_FLAG = '--autostart'              # 由注册表通道拉起时带上的标记
STARTUP_LNK_FLAG = '--autostart-startup'    # 由启动文件夹通道拉起时带上的标记

# 配色：炭黑描边 + 银白主体 + 琥珀/珊瑚点缀（透明背景上任意底色都清晰）
C_OUT = (18, 24, 34, 255)          # 炭黑：所有元素的描边 / 光晕
C_RING = (160, 171, 190, 255)      # 银白：表圈主体
C_RING_IN = (96, 106, 124, 255)    # 冷灰：表圈内侧细圈
C_TICK_BIG = (240, 244, 250, 255)  # 亮白：整点刻度
C_TICK_SM = (148, 160, 182, 255)   # 银灰：小刻度
C_NUM = (234, 239, 246, 255)       # 亮白：数字
C_HOUR = (246, 249, 253, 255)      # 银白：时针
C_MIN = (240, 244, 250, 255)       # 银白：分针
C_SEC = (255, 87, 76, 255)         # 珊瑚红：秒针
C_DATE = (214, 222, 233, 255)      # 冷白：日期 / 星期（两者同色）
C_CAP = (246, 249, 253, 255)       # 银白：中心帽外圈
C_CAP_IN = (26, 31, 40, 255)       # 炭黑：中心帽内点

FONT_DIR = r'C:\Windows\Fonts'
WEEKS = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']


def _make_dpi_aware():
    """声明 DPI 感知，避免系统把窗口内容放大导致边缘发糊、坐标错位。
    必须在创建任何窗口之前调用。"""
    try:
        # 优先：系统级 DPI 感知（对 Tk 最稳妥）
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------- 配置持久化（窗口位置 / 置顶 / 秒针模式） ----------
def _config_path():
    """配置文件路径：优先脚本同目录（便携）；不可写则回退到 %APPDATA%\\DesktopClock。"""
    p = os.path.join(BASE_DIR, CONFIG_FILE)
    try:
        with open(p, 'a'):
            pass
        return p
    except OSError:
        d = os.path.join(os.environ.get('APPDATA', ''), 'DesktopClock')
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, CONFIG_FILE)


def load_config():
    try:
        with open(_config_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    try:
        with open(_config_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _log(msg):
    """关键事件写日志：程序用 pythonw 启动时没有控制台，出错只能靠日志留痕。"""
    try:
        with open(os.path.join(BASE_DIR, LOG_FILE), 'a', encoding='utf-8') as f:
            f.write(f'{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n')
    except OSError:
        pass


_INSTANCE_MUTEX = None      # 保持引用：进程存活期间不释放互斥体


def _single_instance():
    """保证同一时间只有一个时钟在跑（自启动有两条通道，避免开出两个窗口）。"""
    global _INSTANCE_MUTEX
    try:
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = ctypes.c_void_p
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _INSTANCE_MUTEX = k32.CreateMutexW(None, False,
                                           'Local\\DesktopClock_SingleInstance')
        if not _INSTANCE_MUTEX:
            return True
        return k32.GetLastError() != 183        # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _log_startup():
    """记录本次是怎么被拉起来的，方便重启后回看自启动到底有没有生效。"""
    src = '手动启动'
    if AUTOSTART_FLAG in sys.argv:
        src = '注册表自启动'
    elif STARTUP_LNK_FLAG in sys.argv:
        src = '启动文件夹自启动'
    _log(f'启动（{src}，PID={os.getpid()}）')


# ---------- 开机自启动（注册表 Run 键 + 启动文件夹快捷方式，双通道） ----------
def _pythonw():
    """解释器路径：优先 pythonw.exe（无控制台窗口）。"""
    exe = sys.executable or 'pythonw.exe'
    if exe.lower().endswith('python.exe'):
        alt = exe[:-4] + 'w.exe'     # 同目录通常有 pythonw.exe
        if os.path.exists(alt):
            exe = alt
    return exe


def _script_path():
    return os.path.join(BASE_DIR, 'desktop_clock.py')


def _autostart_cmd(flag=AUTOSTART_FLAG):
    """自启动命令行：pythonw + 脚本绝对路径（与工作目录无关，不弹控制台）。"""
    return f'"{_pythonw()}" "{_script_path()}" {flag}'


def _startup_dir():
    """当前用户的"启动"文件夹。"""
    return os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows',
                        'Start Menu', 'Programs', 'Startup')


def _startup_lnk():
    return os.path.join(_startup_dir(), STARTUP_LNK_NAME)


def _is_our_entry(val):
    """判断注册表里的项是不是本程序写的（含搬家 / 换解释器前的旧路径）。"""
    return isinstance(val, str) and 'desktop_clock.py' in val.lower()


def read_autostart_value():
    """读取注册表现有的自启动值；不存在返回 None。"""
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, RUN_VALUE)
        return val
    except OSError:
        return None


def autostart_enabled():
    """只要有一条自启动通道是好的就算开启（任一条被外部清理时，状态显示才不会误导）。"""
    return read_autostart_value() == _autostart_cmd() or startup_lnk_ok()


def _set_run_key(enable):
    """写入 / 删除注册表 Run 键。返回 (是否成功, 错误信息)。"""
    if winreg is None:
        return False, '当前 Python 环境不支持注册表操作（缺少 winreg 模块）'
    try:
        # 同时要读写：删值需 KEY_SET_VALUE，查值需 KEY_QUERY_VALUE
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as k:
            if enable:
                cmd = _autostart_cmd()
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
                val, _ = winreg.QueryValueEx(k, RUN_VALUE)   # 回读校验，防止静默失败
                if val != cmd:
                    return False, '写入后回读不一致（可能被安全软件拦截）'
            else:
                try:
                    val, _ = winreg.QueryValueEx(k, RUN_VALUE)
                except OSError:
                    return True, ''            # 本来就没有，视为已关闭
                # 只删本程序写入的项：包含脚本名的旧路径也算（否则搬家后关不掉）
                if val == _autostart_cmd() or _is_our_entry(val):
                    winreg.DeleteValue(k, RUN_VALUE)
    except OSError as e:
        return False, str(e)
    return True, ''


def startup_lnk_ok():
    """判断启动文件夹里的快捷方式是否指向当前脚本。

    直接读 .lnk 文件里的明文路径做粗略判断，避免解析快捷方式格式、也不启动额外进程。
    """
    try:
        with open(_startup_lnk(), 'rb') as f:
            data = f.read(8192)
    except OSError:
        return False
    target = _script_path()
    for enc in ('utf-16-le', 'mbcs', 'utf-8'):
        try:
            if target.encode(enc) in data:
                return True
        except (LookupError, UnicodeError):
            continue
    return False


def create_startup_lnk():
    """在启动文件夹创建快捷方式（无需管理员权限）。返回 (是否成功, 错误信息)。"""
    def q(s):
        return s.replace("'", "''")      # PowerShell 单引号字符串转义

    try:
        os.makedirs(_startup_dir(), exist_ok=True)
    except OSError as e:
        return False, f'无法访问启动文件夹：{e}'
    ps = (
        "$w=New-Object -ComObject WScript.Shell;"
        f"$s=$w.CreateShortcut('{q(_startup_lnk())}');"
        f"$s.TargetPath='{q(_pythonw())}';"
        f"$s.Arguments='\"{q(_script_path())}\" {STARTUP_LNK_FLAG}';"
        f"$s.WorkingDirectory='{q(BASE_DIR)}';"
        "$s.Save()"
    )
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive',
                            '-Command', ps],
                           capture_output=True, text=True,
                           creationflags=0x08000000)     # CREATE_NO_WINDOW
    except OSError as e:
        return False, f'无法调用 PowerShell：{e}'
    if r.returncode != 0:
        return False, (r.stderr or '').strip() or '创建快捷方式失败'
    return True, ''


def remove_startup_lnk():
    try:
        os.remove(_startup_lnk())
    except FileNotFoundError:
        pass
    except OSError as e:
        return False, str(e)
    return True, ''


def set_autostart(enable):
    """开启 / 关闭开机自启动。返回 (是否成功, 错误信息)。

    同时维护两条通道：注册表 Run 键 + "启动"文件夹快捷方式。
    任一条被安全软件清理或改回旧值，另一条仍能把时钟拉起来。
    """
    ok_run, err_run = _set_run_key(enable)
    ok_lnk, err_lnk = create_startup_lnk() if enable else remove_startup_lnk()
    if ok_run and ok_lnk:
        return True, ''
    if ok_run or ok_lnk:
        msg = f'注册表通道：{err_run or "正常"}；启动文件夹通道：{err_lnk or "正常"}'
        _log(f'自启动只成功了一部分（{msg}）')
        return True, msg          # 有一条通道可用就算成功，细节记日志
    return False, f'注册表：{err_run}；启动文件夹：{err_lnk}'


def sync_autostart_on_start():
    """启动时自愈：任一自启动通道路径过期（项目搬家 / 升级 Python）就修好。

    只在用户开过自启动（注册表项存在）时维护，不会替用户主动开启。
    """
    old = read_autostart_value()
    if old is None:
        return
    if old != _autostart_cmd() and _is_our_entry(old):
        ok, err = _set_run_key(True)
        _log(f'注册表自启动项已修正为当前路径：{old} -> {_autostart_cmd()}'
             if ok else f'注册表自启动项修正失败：{err}')
    if not startup_lnk_ok():
        ok, err = create_startup_lnk()
        _log('启动文件夹快捷方式已创建 / 修正' if ok
             else f'启动文件夹快捷方式创建失败：{err}')


# ---------- 字体 ----------
def load_font(size, *names):
    for n in names:
        try:
            return ImageFont.truetype(os.path.join(FONT_DIR, n), size)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------- Win32 声明 ----------
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class SIZECL(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [('BlendOp', ctypes.c_byte), ('BlendFlags', ctypes.c_byte),
                ('SourceConstantAlpha', ctypes.c_byte), ('AlphaFormat', ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', ctypes.c_uint32), ('biWidth', ctypes.c_int32),
        ('biHeight', ctypes.c_int32), ('biPlanes', ctypes.c_uint16),
        ('biBitCount', ctypes.c_uint16), ('biCompression', ctypes.c_uint32),
        ('biSizeImage', ctypes.c_uint32), ('biXPelsPerMeter', ctypes.c_int32),
        ('biYPelsPerMeter', ctypes.c_int32), ('biClrUsed', ctypes.c_uint32),
        ('biClrImportant', ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER)]


user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, wt.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZECL),
    wt.HDC, ctypes.POINTER(POINT), wt.DWORD, ctypes.POINTER(BLENDFUNCTION), wt.DWORD,
]
user32.UpdateLayeredWindow.restype = wt.BOOL


class DesktopClock:
    def __init__(self):
        _make_dpi_aware()
        cfg = load_config()          # 上次记住的窗口位置 / 置顶 / 秒针模式
        self._cfg = cfg
        sync_autostart_on_start()    # 项目搬家 / 换 Python 后自动修正自启动路径
        self.root = tk.Tk()
        self.root.title('桌面时钟')
        self.smooth = cfg.get('smooth', True)
        self._drag_origin = None
        # 默认不置顶：时钟可被其他窗口遮挡（遮挡部分不显示，时钟照常走时）
        self._top_var = tk.BooleanVar(value=cfg.get('topmost', False))

        self.root.overrideredirect(True)
        self.root.attributes('-topmost', self._top_var.get())
        self.root.resizable(False, False)
        self.root.configure(bg='#000000')
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = cfg.get('x'), cfg.get('y')
        if isinstance(x, int) and isinstance(y, int):
            # 恢复上次的窗口位置
            self.root.geometry(f'{SIZE}x{SIZE}+{x}+{y}')
        else:
            # 首次运行：居中显示
            self.root.geometry(f'{SIZE}x{SIZE}+{(sw - SIZE) // 2}+{(sh - SIZE) // 2}')
        self.root.update()          # 确保窗口已创建，才能取 HWND

        # —— 真实顶层句柄 → 设为分层窗口（逐像素透明）——
        self._hwnd = int(user32.GetParent(int(self.root.winfo_id()))
                         or int(self.root.winfo_id()))
        self._style = user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, self._style | WS_EX_LAYERED)

        # —— GDI 资源（只创建一次，避免每帧开销）——
        self._hdc_screen = user32.GetDC(0)
        self._memdc = gdi32.CreateCompatibleDC(self._hdc_screen)
        bmi = BITMAPINFO()
        hdr = bmi.bmiHeader
        hdr.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        hdr.biWidth = SIZE
        hdr.biHeight = -SIZE        # 自顶向下
        hdr.biPlanes = 1
        hdr.biBitCount = 32
        hdr.biCompression = 0
        self._bits = ctypes.c_void_p()
        self._hbmp = gdi32.CreateDIBSection(self._hdc_screen, ctypes.byref(bmi),
                                            0, ctypes.byref(self._bits), None, 0)
        gdi32.SelectObject(self._memdc, self._hbmp)

        # —— 字体（表盘数字 19px；日期/星期 12px，明显小于表盘数字）——
        # 注意：含中文的字体必须用微软雅黑（Segoe UI 无中文字形）
        self.font_num = load_font(19 * SS, 'seguisb.ttf', 'segoeui.ttf',
                                  'msyhbd.ttc', 'msyh.ttc')
        # 日期与星期同字体同色：微软雅黑加粗（颜色同为 C_DATE）
        self.font_date = load_font(12 * SS, 'msyhbd.ttc', 'msyh.ttc', 'segoeui.ttf')
        self.font_week = self.font_date

        # —— 预渲染静态表盘（表圈 + 刻度 + 数字）——
        self._base = self._render_base()

        # —— 事件 ——
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)
        self.root.bind('<ButtonPress-1>', self._start_drag)
        self.root.bind('<B1-Motion>', self._on_drag)
        self.root.bind('<ButtonRelease-1>', self._end_drag)
        self.root.bind('<Double-Button-1>', lambda e: self.toggle_top())
        self.root.bind('<Button-3>', self._show_menu)

        self._update()
        self.root.mainloop()

    # ---------- 坐标换算 ----------
    @staticmethod
    def _pt(angle, length):
        a = math.radians(angle)
        return math.sin(a) * length, -math.cos(a) * length

    # ---------- 静态表盘 ----------
    def _render_base(self):
        W = H = SIZE * SS
        img = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        cx = cy = SIZE // 2 * SS
        R_ = R * SS

        # 表圈：炭黑描边 + 银白主体 + 冷灰内侧细圈（透明底上任何背景都清晰）
        d = ImageDraw.Draw(img)
        d.ellipse([cx - R_, cy - R_, cx + R_, cy + R_],
                  outline=C_OUT, width=4 * SS)
        d.ellipse([cx - R_, cy - R_, cx + R_, cy + R_],
                  outline=C_RING, width=2 * SS)
        d.ellipse([cx - R_ + 2 * SS, cy - R_ + 2 * SS, cx + R_ - 2 * SS, cy + R_ - 2 * SS],
                  outline=C_RING_IN, width=SS)

        # 60 个刻度，整点为长刻度（炭黑描边 + 亮色主体）
        for i in range(60):
            a = i * 6
            big = (i % 5 == 0)
            inner = (R - 13 if big else R - 7) * SS
            outer = (R - 4) * SS
            dx, dy = self._pt(a, 1)
            x1, y1 = cx + dx * inner, cy + dy * inner
            x2, y2 = cx + dx * outer, cy + dy * outer
            d.line([x1, y1, x2, y2], fill=C_OUT,
                   width=(5 if big else 3) * SS)
            d.line([x1, y1, x2, y2],
                   fill=C_TICK_BIG if big else C_TICK_SM,
                   width=(3 if big else 1) * SS)

        # 数字 1~12（炭黑四向光晕 + 白色主体）
        for i in range(1, 13):
            dx, dy = self._pt(i * 30, R - 36)
            x, y = cx + dx * SS, cy + dy * SS
            for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                d.text((x + ox * SS, y + oy * SS), str(i),
                       font=self.font_num, fill=C_OUT, anchor='mm')
            d.text((x, y), str(i),
                   font=self.font_num, fill=C_NUM, anchor='mm')

        return img

    # ---------- 每帧绘制 ----------
    def _render_frame(self, now):
        img = self._base.copy()
        d = ImageDraw.Draw(img)
        s_ = SS
        cx = cy = SIZE // 2 * s_

        # 当前时间（秒含毫秒插值，精确到秒）
        micro = now.microsecond / 1e6
        s = now.second + (micro if self.smooth else 0)
        m = now.minute + s / 60
        h = (now.hour % 12) + m / 60

        # 日期与星期（炭黑四向光晕 + 主体，先画，指针在其上）
        ds = f'{now.year}年{now.month}月{now.day}日'
        ws = WEEKS[now.weekday()]
        for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.text((cx + ox * s_, (CY + 46) * s_ + oy * s_), ds,
                   font=self.font_date, fill=C_OUT, anchor='mm')
            d.text((cx + ox * s_, (CY + 63) * s_ + oy * s_), ws,
                   font=self.font_week, fill=C_OUT, anchor='mm')
        d.text((cx, (CY + 46) * s_), ds, font=self.font_date,
               fill=C_DATE, anchor='mm')
        d.text((cx, (CY + 63) * s_), ws, font=self.font_week,
               fill=C_DATE, anchor='mm')

        # 时针 / 分针（锥形；polygon 描边沿边缘走，尖端不会伸出表圈）
        hp = self._hand_pts(h * 30, 88, 11, 5, 14)
        mp = self._hand_pts(m * 6, 136, 7, 3, 11)
        d.polygon(hp, fill=C_HOUR, outline=C_OUT, width=2 * s_)
        d.polygon(mp, fill=C_MIN, outline=C_OUT, width=s_)

        # 秒针（珊瑚红细长 + 尾部配重，带炭黑描边）
        a = math.radians(s * 6)
        dx, dy = math.sin(a), -math.cos(a)
        L = (R - 16) * s_
        tl = 26 * s_
        d.line([cx - dx * tl, cy - dy * tl, cx + dx * L, cy + dy * L],
               fill=C_OUT, width=4 * s_)
        d.line([cx - dx * tl, cy - dy * tl, cx + dx * L, cy + dy * L],
               fill=C_SEC, width=2 * s_)
        w_ = 5 * s_
        tx, ty = cx - dx * tl, cy - dy * tl
        d.ellipse([tx - w_ - s_, ty - w_ - s_, tx + w_ + s_, ty + w_ + s_],
                  fill=C_OUT)
        d.ellipse([tx - w_, ty - w_, tx + w_, ty + w_], fill=C_SEC)

        # 中心帽（银白外圈 + 炭黑描边 + 炭黑内点）
        c_ = 7 * s_
        d.ellipse([cx - c_, cy - c_, cx + c_, cy + c_], fill=C_CAP)
        d.ellipse([cx - c_, cy - c_, cx + c_, cy + c_], outline=C_OUT, width=2)
        c2 = 3 * s_
        d.ellipse([cx - c2, cy - c2, cx + c2, cy + c2], fill=C_CAP_IN)

        return img.resize((SIZE, SIZE), Image.LANCZOS)

    def _hand_pts(self, angle, length, base_w, tip_w, tail):
        s_ = SS
        a = math.radians(angle)
        dx, dy = math.sin(a), -math.cos(a)
        px, py = -dy, dx
        cx = cy = SIZE // 2 * s_
        L = length * s_
        b = base_w * s_ / 2
        t = tip_w * s_ / 2
        tl = tail * s_
        x0, y0 = cx - dx * tl, cy - dy * tl
        return [(x0 + px * b, y0 + py * b), (x0 - px * b, y0 - py * b),
                (cx + dx * L - px * t, cy + dy * L - py * t),
                (cx + dx * L + px * t, cy + dy * L + py * t)]

    # ---------- 透明合成显示 ----------
    def _blit(self, img):
        # Pillow 'BGRa' raw 模式：直接输出 BGRA 预乘字节（C 速度）
        bgra = img.convert('RGBA').tobytes('raw', 'BGRa')
        ctypes.memmove(self._bits, bgra, len(bgra))
        pt_src = POINT(0, 0)
        sz = SIZECL(SIZE, SIZE)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        # ptDst 传 NULL：只原位更新分层内容、不移动窗口，
        # 窗口位置完全由 Tk 的 geometry 控制（避免 ULW 与窗口管理器抢位置导致漂移）
        user32.UpdateLayeredWindow(self._hwnd, self._hdc_screen,
                                   None, ctypes.byref(sz),
                                   self._memdc, ctypes.byref(pt_src),
                                   0, ctypes.byref(blend), ULW_ALPHA)

    # ---------- 主循环 ----------
    def _update(self):
        self._blit(self._render_frame(datetime.datetime.now()))
        self.root.after(50, self._update)

    # ---------- 拖拽 ----------
    def _start_drag(self, e):
        self._drag_origin = (e.x_root, e.y_root)

    def _on_drag(self, e):
        if not self._drag_origin:
            return
        ox, oy = self._drag_origin
        dx, dy = e.x_root - ox, e.y_root - oy
        if dx or dy:
            x, y = self.root.winfo_x(), self.root.winfo_y()
            self.root.geometry(f'+{x + dx}+{y + dy}')
            self._drag_origin = (e.x_root, e.y_root)

    def _end_drag(self, e):
        self._drag_origin = None
        # 拖动结束即记住位置：即使之后进程被强制结束，下次也能恢复
        self._cfg.update(x=self.root.winfo_x(), y=self.root.winfo_y())
        save_config(self._cfg)

    # ---------- 置顶 ----------
    def toggle_top(self):
        self._top_var.set(not self._top_var.get())
        self.root.attributes('-topmost', self._top_var.get())

    # ---------- 开机自启动 ----------
    def _toggle_autostart(self, var):
        """勾选 / 取消开机自启动；失败时给出可见提示（pythonw 下没有控制台）。"""
        ok, err = set_autostart(bool(var.get()))
        if ok:
            return
        var.set(not var.get())      # 回滚勾选状态，避免显示与实际不符
        _log(f'设置开机自启动失败：{err}')
        messagebox.showerror('桌面时钟', f'开机自启动设置失败：\n{err}')

    # ---------- 右键菜单 ----------
    def _show_menu(self, e):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_checkbutton(label='窗口置顶', variable=self._top_var,
                             command=lambda: self.root.attributes('-topmost',
                                                                  self._top_var.get()))
        sv = tk.BooleanVar(value=self.smooth)
        mode = tk.Menu(menu, tearoff=0)
        mode.add_radiobutton(label='平滑走动（丝滑）', variable=sv, value=True,
                             command=lambda: setattr(self, 'smooth', True))
        mode.add_radiobutton(label='每秒跳动（经典）', variable=sv, value=False,
                             command=lambda: setattr(self, 'smooth', False))
        menu.add_cascade(label='秒针模式', menu=mode)
        au = tk.BooleanVar(value=autostart_enabled())
        menu.add_checkbutton(label='开机自启动', variable=au,
                             command=lambda: self._toggle_autostart(au))
        menu.add_separator()
        menu.add_command(label='退出', command=self._on_close)

        self._menu = menu
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    # ---------- 关闭 ----------
    def _on_close(self):
        """退出前保存窗口位置与设置，再销毁窗口。"""
        self._cfg.update(
            x=self.root.winfo_x(),
            y=self.root.winfo_y(),
            topmost=self._top_var.get(),
            smooth=self.smooth,
        )
        save_config(self._cfg)
        self.root.destroy()


if __name__ == '__main__':
    _log_startup()
    if not _single_instance():
        _log('已有实例在运行，本次启动退出')
        sys.exit(0)
    DesktopClock()
