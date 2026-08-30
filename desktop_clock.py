# -*- coding: utf-8 -*-
"""
桌面时钟 —— 表盘式 v2
- Pillow 3x 超采样渲染 + LANCZOS 缩放，消除边缘锯齿
- Win32 UpdateLayeredWindow 实现真正的逐像素透明（无方框、无白底）
- 表盘面完全透明：只显示表圈、刻度、数字、指针，透过表盘可见桌面
- 秒针精确到秒（平滑走动，每秒准确落位）
- 表盘下半部分以数字显示 年月日 与 星期（字号小于表盘数字）
- 无边框、置顶、可拖动

运行方式：python desktop_clock.py
操作：左键按住拖动；双击切换置顶；右键菜单（置顶 / 秒针模式 / 退出）
"""

import ctypes
import datetime
import math
import os
import tkinter as tk
from ctypes import wintypes as wt
from PIL import Image, ImageDraw, ImageFont

# ============ 配置 ============
SIZE = 360                 # 窗口边长（逻辑像素）
CX, CY = SIZE // 2, SIZE // 2
R = 162                    # 表盘半径
SS = 3                     # 超采样倍数（抗锯齿）

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
C_DATE = (214, 222, 233, 255)      # 冷白：日期
C_WEEK = (255, 189, 92, 255)       # 琥珀：星期
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
        self.root = tk.Tk()
        self.root.title('桌面时钟')
        self.smooth = True
        self._drag_origin = None
        self._top_var = tk.BooleanVar(value=True)

        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.resizable(False, False)
        self.root.configure(bg='#000000')
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
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
        self.font_date = load_font(12 * SS, 'msyh.ttc', 'segoeui.ttf', 'msyhbd.ttc')
        self.font_week = load_font(12 * SS, 'msyhbd.ttc', 'msyh.ttc')

        # —— 预渲染静态表盘（表圈 + 刻度 + 数字）——
        self._base = self._render_base()

        # —— 事件 ——
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
               fill=C_WEEK, anchor='mm')

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

    # ---------- 置顶 ----------
    def toggle_top(self):
        self._top_var.set(not self._top_var.get())
        self.root.attributes('-topmost', self._top_var.get())

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
        menu.add_separator()
        menu.add_command(label='退出', command=self.root.destroy)

        self._menu = menu
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()


if __name__ == '__main__':
    DesktopClock()
