#!/usr/bin/env python3
"""Realtime graphical front end for net_stability_test.py."""

from __future__ import annotations

import argparse
import ctypes
import csv
import datetime as dt
import http.client
import json
import math
import os
import re
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from ctypes import wintypes
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Optional

from PIL import Image, ImageDraw, ImageTk

from net_stability_test import (
    SharedState,
    download_worker,
    infer_latency_host,
    latency_worker,
    mbps,
    now_iso,
    percentile,
)


DIRECT_DOWNLOAD_URL = (
    "https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/"
    "ubuntu-24.04.4-desktop-amd64.iso"
)


def enable_high_dpi() -> None:
    """Enable crisp native rendering before the first Tk window is created."""
    if os.name != "nt":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(
            ctypes.c_void_p(-4)
        ):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
DIRECT_PROFILE = "直连国内"
PROXY_PROFILE = "Clash 代理（国际）"
PROFILES = {
    DIRECT_PROFILE: {
        "download_url": DIRECT_DOWNLOAD_URL,
        "download_payload": 100_000_000,
        "download_timeout": 20.0,
        "download_direct": True,
        "upload_host": "speedtest1.online.sh.cn",
        "upload_port": 8080,
        "upload_path": "/speedtest/upload.php",
        "upload_use_proxy": False,
        "upload_payload": 32 * 1024 * 1024,
        "upload_timeout": 20.0,
        "log_slug": "direct",
        "info": (
            "下载源：中国科学技术大学镜像；上传源：上海电信 Speedtest。"
            "忽略 Windows/环境 HTTP 代理。"
        ),
    },
    PROXY_PROFILE: {
        "download_url": (
            "https://librespeed.a573.net/backend/"
            "garbage.php?ckSize=10&r={cachebust}"
        ),
        "download_payload": 10_000_000,
        "download_timeout": 60.0,
        "download_direct": False,
        "upload_host": "librespeed.a573.net",
        "upload_port": 443,
        "upload_path": "/backend/empty.php",
        "upload_use_proxy": True,
        "upload_payload": 1_000_000,
        "upload_timeout": 60.0,
        "latency_url": (
            "https://librespeed.a573.net/backend/empty.php?r={cachebust}"
        ),
        "log_slug": "clash_proxy",
        "info": (
            "下载与上传源：LibreSpeed 东京节点；使用 Windows 系统代理。"
            "请在 Clash 连接列表确认 librespeed.a573.net 命中代理节点。"
        ),
    },
}
PROXY_SOURCE_TOKYO = "东京（A573）"
PROXY_SOURCE_LA = "洛杉矶（Clouvider）"
PROXY_SOURCE_FRANKFURT = "法兰克福（Clouvider）"
PROXY_SOURCES = {
    PROXY_SOURCE_TOKYO: {
        "download_url": (
            "https://librespeed.a573.net/backend/"
            "garbage.php?ckSize=10&r={cachebust}"
        ),
        "upload_host": "librespeed.a573.net",
        "upload_path": "/backend/empty.php",
        "latency_url": (
            "https://librespeed.a573.net/backend/empty.php?r={cachebust}"
        ),
        "log_slug": "clash_proxy_tokyo",
        "info": (
            "代理测速源：LibreSpeed 东京 A573；使用 Windows 系统代理。"
            "请确认 librespeed.a573.net 在 Clash 中命中代理节点。"
        ),
    },
    PROXY_SOURCE_LA: {
        "download_url": (
            "https://la.speedtest.clouvider.net/backend/"
            "garbage.php?ckSize=10&r={cachebust}"
        ),
        "upload_host": "la.speedtest.clouvider.net",
        "upload_path": "/backend/empty.php",
        "latency_url": (
            "https://la.speedtest.clouvider.net/backend/empty.php?r={cachebust}"
        ),
        "log_slug": "clash_proxy_los_angeles",
        "info": (
            "代理测速源：LibreSpeed 洛杉矶 Clouvider；使用 Windows 系统代理。"
            "请确认 la.speedtest.clouvider.net 在 Clash 中命中代理节点。"
        ),
    },
    PROXY_SOURCE_FRANKFURT: {
        "download_url": (
            "https://fra.speedtest.clouvider.net/backend/"
            "garbage.php?ckSize=10&r={cachebust}"
        ),
        "upload_host": "fra.speedtest.clouvider.net",
        "upload_path": "/backend/empty.php",
        "latency_url": (
            "https://fra.speedtest.clouvider.net/backend/empty.php?r={cachebust}"
        ),
        "log_slug": "clash_proxy_frankfurt",
        "info": (
            "代理测速源：LibreSpeed 法兰克福 Clouvider；使用 Windows 系统代理。"
            "请确认 fra.speedtest.clouvider.net 在 Clash 中命中代理节点。"
        ),
    },
}
DOWNLOAD_ONLY = "仅下载"
UPLOAD_ONLY = "仅上传"
DUPLEX_TEST = "下载 + 上传同时"
WEB_ONLY = "仅网页体验"
TEST_KIND_SLUGS = {
    DOWNLOAD_ONLY: "download_only",
    UPLOAD_ONLY: "upload_only",
    DUPLEX_TEST: "duplex",
    WEB_ONLY: "web_only",
}
WEB_PROFILE_MIXED = "综合网页体验"
WEB_PROFILE_CHATGPT = "ChatGPT 专项"
WEB_PROFILE_COMMON = "常用海外网站"
WEB_PROFILE_OFF = "关闭网页测试"
WEB_TEST_PROFILES = {
    WEB_PROFILE_MIXED: (
        ("ChatGPT", "https://chatgpt.com/"),
        ("Google", "https://www.google.com/generate_204"),
        ("GitHub", "https://github.com/favicon.ico"),
        ("Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
    ),
    WEB_PROFILE_CHATGPT: (
        ("ChatGPT", "https://chatgpt.com/"),
        ("OpenAI Auth", "https://auth.openai.com/"),
    ),
    WEB_PROFILE_COMMON: (
        ("Google", "https://www.google.com/generate_204"),
        ("GitHub", "https://github.com/favicon.ico"),
        ("Cloudflare", "https://www.cloudflare.com/cdn-cgi/trace"),
        ("Microsoft", "https://www.microsoft.com/favicon.ico"),
    ),
    WEB_PROFILE_OFF: (),
}
IP_LOOKUP_URL = (
    "http://ip-api.com/json/"
    "?lang=zh-CN&fields=status,message,query,country,regionName,city,isp"
)
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_ICON_PNG = os.path.join(APP_DIR, "net_stability_icon_minimal.png")
APP_ICON_ICO = os.path.join(APP_DIR, "net_stability_icon_minimal.ico")
APP_DATA_DIR = r"D:\ProgramData\NetworkStabilityTest"
LOG_DIR = os.path.join(APP_DATA_DIR, "logs")
UI_SETTINGS_PATH = os.path.join(APP_DATA_DIR, "ui_settings.json")


def windows_prefers_dark_mode() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return int(value) == 0
    except (ImportError, OSError, ValueError):
        return False


def load_ui_settings() -> dict:
    try:
        with open(UI_SETTINGS_PATH, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, dict):
            return value
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return {}


def save_ui_setting(key: str, value) -> None:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    settings = load_ui_settings()
    settings[key] = value
    try:
        with open(UI_SETTINGS_PATH, "w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
    except OSError:
        pass


def load_ui_theme() -> str:
    value = load_ui_settings().get("theme")
    if value in ("light", "dark"):
        return str(value)
    return "dark" if windows_prefers_dark_mode() else "light"


def save_ui_theme(theme: str) -> None:
    save_ui_setting("theme", theme)


def load_sidebar_width() -> int:
    try:
        width = int(load_ui_settings().get("sidebar_width", 208))
    except (TypeError, ValueError):
        width = 208
    return max(188, min(340, width))


def save_sidebar_width(width: int) -> None:
    save_ui_setting("sidebar_width", max(188, min(340, int(width))))


def blend_color(start: str, end: str, amount: float) -> str:
    amount = max(0.0, min(1.0, amount))
    start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
    end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
    values = tuple(
        round(left + (right - left) * amount)
        for left, right in zip(start_rgb, end_rgb)
    )
    return "#{:02x}{:02x}{:02x}".format(*values)


def rounded_polygon_points(width: int, height: int, radius: int) -> tuple[int, ...]:
    inset = 1
    radius = min(radius, max(1, (height - 2 * inset) // 2))
    x1, y1 = inset, inset
    x2, y2 = width - inset, height - inset
    return (
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1,
    )


def rounded_box_points(
    x1: int | float,
    y1: int | float,
    x2: int | float,
    y2: int | float,
    radius: int,
) -> tuple[float, ...]:
    points = rounded_polygon_points(
        max(2, round(x2 - x1)),
        max(2, round(y2 - y1)),
        radius,
    )
    return tuple(
        value + (x1 if index % 2 == 0 else y1)
        for index, value in enumerate(points)
    )


class RoundedPanel(tk.Canvas):
    """Canvas-backed rounded surface with a normal Tk frame inside."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        parent_background: str,
        fill: str,
        border: str,
        radius: int,
        padding: int = 0,
        fixed_height: Optional[int] = None,
    ) -> None:
        super().__init__(
            master,
            background=parent_background,
            borderwidth=0,
            highlightthickness=0,
            height=fixed_height or 1,
        )
        self.panel_fill = fill
        self.panel_border = border
        self.radius = radius
        self.panel_padding = padding
        self.fixed_height = fixed_height
        self.inner = tk.Frame(self, background=fill, borderwidth=0)
        self.inner_window = self.create_window(
            padding,
            padding,
            anchor="nw",
            window=self.inner,
        )
        self.bind("<Configure>", self._redraw, add="+")
        self.inner.bind("<Configure>", self._fit_to_content, add="+")

    def _fit_to_content(self, _event=None) -> None:
        if self.fixed_height is None and not self.winfo_manager():
            return
        if self.fixed_height is None and self.winfo_height() <= 1:
            self.configure(
                height=self.inner.winfo_reqheight() + 2 * self.panel_padding
            )

    def _redraw(self, _event=None) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        points = rounded_polygon_points(width, height, self.radius)
        self.delete("rounded_surface")
        self.create_polygon(
            points,
            smooth=True,
            splinesteps=24,
            fill=self.panel_fill,
            outline=self.panel_border,
            width=1,
            tags="rounded_surface",
        )
        self.tag_lower("rounded_surface")
        inner_width = max(1, width - 2 * self.panel_padding)
        inner_height = max(1, height - 2 * self.panel_padding)
        self.itemconfigure(
            self.inner_window,
            width=inner_width,
            height=inner_height,
        )


class AnimatedRoundedButton(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        command,
        width: int,
        height: int,
        radius: int,
        parent_background: str,
        fill: str,
        hover_fill: str,
        pressed_fill: str,
        disabled_fill: str,
        foreground: str,
        disabled_foreground: str,
        font: tkfont.Font,
        state: str = "normal",
        alignment: str = "center",
        leading_icon: str = "",
        trailing_text: str = "",
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background=parent_background,
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2" if state != "disabled" else "arrow",
        )
        self._button_text = text
        self._command = command
        self._radius = radius
        self._normal_fill = fill
        self._hover_fill = hover_fill
        self._pressed_fill = pressed_fill
        self._disabled_fill = disabled_fill
        self._foreground = foreground
        self._disabled_foreground = disabled_foreground
        self._font = font
        self._button_state = state
        self._alignment = alignment
        self._leading_icon = leading_icon
        self._trailing_text = trailing_text
        self._current_fill = disabled_fill if state == "disabled" else fill
        self._animation_id: Optional[str] = None
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _draw(self) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self.delete("button")
        self.create_polygon(
            rounded_polygon_points(width, height, self._radius),
            smooth=True,
            splinesteps=28,
            fill=self._current_fill,
            outline=self._current_fill,
            tags="button",
        )
        foreground = (
            self._disabled_foreground
            if self._button_state == "disabled"
            else self._foreground
        )
        left_aligned = self._alignment == "left"
        text_x = height * 0.96 if left_aligned else width / 2
        self.create_text(
            text_x,
            height / 2,
            text=self._button_text,
            fill=foreground,
            font=self._font,
            anchor="w" if left_aligned else "center",
            tags="button",
        )
        if left_aligned and self._leading_icon:
            self.create_text(
                height * 0.48,
                height / 2,
                text=self._leading_icon,
                fill=foreground,
                font=self._font,
                tags="button",
            )
        if left_aligned and self._trailing_text:
            self.create_text(
                width - height * 0.46,
                height / 2,
                text=self._trailing_text,
                fill=foreground,
                font=self._font,
                anchor="e",
                tags="button",
            )

    def _animate_to(self, target: str, duration_ms: int = 120) -> None:
        if self._animation_id is not None:
            try:
                self.after_cancel(self._animation_id)
            except tk.TclError:
                pass
        start = self._current_fill
        steps = 7

        def frame(step: int = 1) -> None:
            self._current_fill = blend_color(start, target, step / steps)
            self._draw()
            if step < steps:
                self._animation_id = self.after(
                    max(12, duration_ms // steps),
                    frame,
                    step + 1,
                )
            else:
                self._animation_id = None

        frame()

    def _enter(self, _event=None) -> None:
        if self._button_state != "disabled":
            self._animate_to(self._hover_fill)

    def _leave(self, _event=None) -> None:
        if self._button_state != "disabled":
            self._animate_to(self._normal_fill)

    def _press(self, _event=None) -> None:
        if self._button_state != "disabled":
            self._animate_to(self._pressed_fill, 70)

    def _release(self, event: tk.Event) -> None:
        if self._button_state == "disabled":
            return
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        self._animate_to(self._hover_fill if inside else self._normal_fill, 90)
        if inside and self._command is not None:
            self._command()

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        state = kwargs.pop("state", None)
        text = kwargs.pop("text", None)
        trailing_text = kwargs.pop("trailing_text", None)
        fill = kwargs.pop("fill", None)
        foreground = kwargs.pop("foreground", None)
        if state is not None:
            self._button_state = str(state)
            super().configure(cursor="arrow" if state == "disabled" else "hand2")
            target = self._disabled_fill if state == "disabled" else self._normal_fill
            self._current_fill = target
            self._draw()
        if text is not None:
            self._button_text = str(text)
            self._draw()
        if trailing_text is not None:
            self._trailing_text = str(trailing_text)
            self._draw()
        if fill is not None:
            self._normal_fill = str(fill)
            self._current_fill = (
                self._disabled_fill
                if self._button_state == "disabled"
                else self._normal_fill
            )
            self._draw()
        if foreground is not None:
            self._foreground = str(foreground)
            self._draw()
        if kwargs:
            return super().configure(**kwargs)
        return None

    config = configure

    def cget(self, key: str):
        if key == "state":
            return self._button_state
        if key == "text":
            return self._button_text
        if key == "trailing_text":
            return self._trailing_text
        return super().cget(key)


class AnimatedSelectTile(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        icon: str,
        variable: tk.StringVar,
        values: tuple[str, ...],
        width: int,
        height: int,
        radius: int,
        palette: dict[str, str],
        font: tkfont.Font,
        small_font: tkfont.Font,
        accent: str,
        state: str = "normal",
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background=palette["sidebar"],
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2" if state != "disabled" else "arrow",
        )
        self._title = title
        self._icon = icon
        self._variable = variable
        self._values = values
        self._radius = radius
        self._palette = palette
        self._font = font
        self._small_font = small_font
        self._accent = accent
        self._tile_state = state
        self._normal_fill = palette["tile"]
        self._hover_fill = palette["tile_hover"]
        self._pressed_fill = palette["tile_pressed"]
        self._current_fill = (
            palette["tile_disabled"] if state == "disabled" else self._normal_fill
        )
        self._animation_id: Optional[str] = None
        self._popup: Optional[tk.Toplevel] = None
        self._popup_alpha_id: Optional[str] = None
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<Enter>", lambda _event: self._animate_to(self._hover_fill))
        self.bind("<Leave>", lambda _event: self._animate_to(self._normal_fill))
        self.bind("<ButtonPress-1>", lambda _event: self._animate_to(self._pressed_fill, 70))
        self.bind("<ButtonRelease-1>", self._open_menu)
        self._variable_trace_id = self._variable.trace_add(
            "write",
            self._variable_changed,
        )
        self.bind("<Destroy>", self._on_destroy, add="+")
        self._draw()

    def _variable_changed(self, *_args) -> None:
        try:
            if self.winfo_exists():
                self._draw()
        except tk.TclError:
            pass

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        try:
            self._variable.trace_remove("write", self._variable_trace_id)
        except (tk.TclError, AttributeError):
            pass
        if self._popup_alpha_id is not None:
            try:
                self.after_cancel(self._popup_alpha_id)
            except tk.TclError:
                pass
            self._popup_alpha_id = None
        popup = self._popup
        self._popup = None
        if popup is not None:
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except tk.TclError:
                pass

    def _draw(self) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        disabled = self._tile_state == "disabled"
        title_color = self._palette["subtle"]
        value_color = self._palette["muted"] if disabled else self._palette["text"]
        icon_x = height * 0.34
        icon_radius = height * 0.18
        text_x = height * 0.76
        icon_color = self._palette["subtle"] if disabled else self._accent
        icon_fill = blend_color(
            self._current_fill,
            self._palette["subtle"] if disabled else self._accent,
            0.12,
        )
        self.delete("tile")
        self.create_polygon(
            rounded_polygon_points(width, height, self._radius),
            smooth=True,
            splinesteps=28,
            fill=self._current_fill,
            outline=self._current_fill,
            tags="tile",
        )
        self.create_oval(
            icon_x - icon_radius,
            height / 2 - icon_radius,
            icon_x + icon_radius,
            height / 2 + icon_radius,
            fill=icon_fill,
            outline=icon_fill,
            tags="tile",
        )
        self.create_text(
            icon_x,
            height / 2,
            text=self._icon,
            fill=icon_color,
            font=self._small_font,
            tags="tile",
        )
        self.create_text(
            text_x,
            height * 0.32,
            text=self._title,
            fill=title_color,
            font=self._small_font,
            anchor="w",
            tags="tile",
        )
        self.create_text(
            text_x,
            height * 0.69,
            text=self._variable.get(),
            fill=value_color,
            font=self._font,
            anchor="w",
            tags="tile",
        )
        self.create_text(
            width - height * 0.28,
            height / 2,
            text="⌄",
            fill=self._palette["subtle"],
            font=self._font,
            anchor="e",
            tags="tile",
        )

    def _animate_to(self, target: str, duration_ms: int = 140) -> None:
        if self._tile_state == "disabled":
            return
        if self._animation_id is not None:
            try:
                self.after_cancel(self._animation_id)
            except tk.TclError:
                pass
        start = self._current_fill
        steps = 8

        def frame(step: int = 1) -> None:
            self._current_fill = blend_color(start, target, step / steps)
            self._draw()
            if step < steps:
                self._animation_id = self.after(
                    max(12, duration_ms // steps),
                    frame,
                    step + 1,
                )
            else:
                self._animation_id = None

        frame()

    def _open_menu(self, event: tk.Event) -> None:
        if self._tile_state == "disabled":
            return
        inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if not inside:
            self._animate_to(self._normal_fill)
            return
        if self._popup is not None and self._popup.winfo_exists():
            self._dismiss_popup()
            return

        self.update_idletasks()
        tile_width = max(120, self.winfo_width())
        tile_height = max(44, self.winfo_height())
        row_height = max(34, round(tile_height * 0.68))
        outer_padding = max(5, round(tile_height * 0.08))
        popup_height = outer_padding * 2 + row_height * len(self._values)
        popup_x = self.winfo_rootx()
        popup_y = self.winfo_rooty() + tile_height + max(4, outer_padding // 2)
        screen_bottom = self.winfo_vrooty() + self.winfo_vrootheight()
        if popup_y + popup_height > screen_bottom - outer_padding:
            popup_y = self.winfo_rooty() - popup_height - max(4, outer_padding // 2)

        popup = tk.Toplevel(self)
        self._popup = popup
        popup.withdraw()
        popup.overrideredirect(True)
        popup.transient(self.winfo_toplevel())
        transparent = "#010203"
        popup.configure(background=transparent)
        try:
            popup.wm_attributes("-transparentcolor", transparent)
            popup.wm_attributes("-alpha", 0.0)
        except tk.TclError:
            popup.configure(background=self._palette["surface"])
        popup.geometry(
            f"{tile_width}x{popup_height}+{popup_x}+{max(0, popup_y)}"
        )
        surface = RoundedPanel(
            popup,
            parent_background=transparent,
            fill=self._palette["surface"],
            border=self._palette["border"],
            radius=max(14, round(tile_height * 0.18)),
            padding=outer_padding,
            fixed_height=popup_height,
        )
        surface.pack(fill="both", expand=True)
        current = self._variable.get()
        for value in self._values:
            selected = value == current
            row = AnimatedRoundedButton(
                surface.inner,
                text=value,
                command=lambda item=value: self._choose_from_popup(item),
                width=max(80, tile_width - outer_padding * 2),
                height=row_height,
                radius=max(10, round(row_height * 0.30)),
                parent_background=self._palette["surface"],
                fill=(
                    blend_color(self._palette["surface"], self._accent, 0.18)
                    if selected
                    else self._palette["surface"]
                ),
                hover_fill=self._palette["tile_hover"],
                pressed_fill=self._palette["tile_pressed"],
                disabled_fill=self._palette["tile_disabled"],
                foreground=self._palette["text"],
                disabled_foreground=self._palette["subtle"],
                font=self._font,
                alignment="left",
                leading_icon="✓" if selected else "",
            )
            row.pack(fill="x")

        popup.bind("<Escape>", lambda _event: self._dismiss_popup())
        popup.bind("<FocusOut>", self._popup_focus_out, add="+")
        popup.deiconify()
        popup.lift()
        popup.focus_force()
        self._fade_popup(0.0)

    def _fade_popup(self, alpha: float) -> None:
        popup = self._popup
        if popup is None or not popup.winfo_exists():
            self._popup_alpha_id = None
            return
        next_alpha = min(1.0, alpha + 0.18)
        try:
            popup.wm_attributes("-alpha", next_alpha)
        except tk.TclError:
            self._popup_alpha_id = None
            return
        if next_alpha < 1.0:
            self._popup_alpha_id = self.after(
                16,
                self._fade_popup,
                next_alpha,
            )
        else:
            self._popup_alpha_id = None

    def _popup_focus_out(self, _event=None) -> None:
        self.after(25, self._dismiss_popup_if_unfocused)

    def _dismiss_popup_if_unfocused(self) -> None:
        popup = self._popup
        if popup is None or not popup.winfo_exists():
            return
        focus = popup.focus_get()
        if focus is None or not str(focus).startswith(str(popup)):
            self._dismiss_popup()

    def _dismiss_popup(self) -> None:
        if self._popup_alpha_id is not None:
            try:
                self.after_cancel(self._popup_alpha_id)
            except tk.TclError:
                pass
            self._popup_alpha_id = None
        popup = self._popup
        self._popup = None
        if popup is not None and popup.winfo_exists():
            popup.destroy()
        self._animate_to(self._normal_fill)

    def _choose_from_popup(self, value: str) -> None:
        self._dismiss_popup()
        self._select(value)

    def _select(self, value: str) -> None:
        self._variable.set(value)
        self.event_generate("<<ComboboxSelected>>")
        self._current_fill = blend_color(self._normal_fill, self._accent, 0.12)
        self._draw()
        self.after(90, lambda: self._animate_to(self._normal_fill, 180))

    def configure(self, cnf=None, **kwargs):
        if cnf:
            kwargs.update(cnf)
        state = kwargs.pop("state", None)
        if state is not None:
            self._tile_state = str(state)
            if state == "disabled":
                self._dismiss_popup()
            self._current_fill = (
                self._palette["tile_disabled"]
                if state == "disabled"
                else self._normal_fill
            )
            super().configure(cursor="arrow" if state == "disabled" else "hand2")
            self._draw()
        if kwargs:
            return super().configure(**kwargs)
        return None

    config = configure

    def cget(self, key: str):
        if key == "state":
            return self._tile_state
        return super().cget(key)


class RoundedEntryTile(tk.Canvas):
    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        icon: str,
        variable: tk.StringVar,
        suffix: str,
        width: int,
        height: int,
        radius: int,
        palette: dict[str, str],
        font: tkfont.Font,
        small_font: tkfont.Font,
        accent: str,
        parent_background: Optional[str] = None,
    ) -> None:
        super().__init__(
            master,
            width=width,
            height=height,
            background=parent_background or palette["sidebar"],
            borderwidth=0,
            highlightthickness=0,
        )
        self._title = title
        self._icon = icon
        self._suffix = suffix
        self._radius = radius
        self._palette = palette
        self._font = font
        self._small_font = small_font
        self._accent = accent
        self.entry = tk.Entry(
            self,
            textvariable=variable,
            background=palette["tile"],
            foreground=palette["text"],
            disabledbackground=palette["tile_disabled"],
            disabledforeground=palette["subtle"],
            insertbackground=palette["text"],
            selectbackground=accent,
            selectforeground="#ffffff",
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
            justify="right",
            font=font,
        )
        self._entry_window = self.create_window(
            1,
            1,
            window=self.entry,
            anchor="e",
        )
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self._draw()

    def _draw(self) -> None:
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        icon_x = height * 0.34
        icon_radius = height * 0.18
        text_x = height * 0.76
        self.delete("tile")
        self.create_polygon(
            rounded_polygon_points(width, height, self._radius),
            smooth=True,
            splinesteps=28,
            fill=self._palette["tile"],
            outline=self._palette["tile"],
            tags="tile",
        )
        self.tag_lower("tile")
        self.create_oval(
            icon_x - icon_radius,
            height / 2 - icon_radius,
            icon_x + icon_radius,
            height / 2 + icon_radius,
            fill=blend_color(self._palette["tile"], self._accent, 0.12),
            outline=blend_color(self._palette["tile"], self._accent, 0.12),
            tags="tile",
        )
        self.create_text(
            icon_x,
            height / 2,
            text=self._icon,
            fill=self._accent,
            font=self._small_font,
            tags="tile",
        )
        self.create_text(
            text_x,
            height * 0.32,
            text=self._title,
            fill=self._palette["subtle"],
            font=self._small_font,
            anchor="w",
            tags="tile",
        )
        self.create_text(
            width - height * 0.28,
            height * 0.69,
            text=self._suffix,
            fill=self._palette["subtle"],
            font=self._small_font,
            anchor="e",
            tags="tile",
        )
        self.coords(self._entry_window, width - height * 0.70, height * 0.68)
        self.itemconfigure(
            self._entry_window,
            width=max(40, int(height * 1.42)),
            height=max(22, height // 2),
        )


def optional_float(value) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def summarize_history_log(path: str) -> Optional[dict]:
    last_row: Optional[dict] = None
    latency_samples: list[float] = []
    web_samples: list[float] = []
    try:
        with open(path, "r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                last_row = row
                latency = optional_float(row.get("latency_avg_ms"))
                if latency is not None:
                    latency_samples.append(latency)
                web_ttfb = optional_float(row.get("web_ttfb_ms"))
                if web_ttfb is not None:
                    web_samples.append(web_ttfb)
    except (OSError, csv.Error):
        return None

    if last_row is None:
        return None

    filename = os.path.basename(path)
    lower_name = filename.lower()
    timestamp_match = re.search(r"_(\d{8})_(\d{6})\.csv$", filename)
    if timestamp_match:
        started = dt.datetime.strptime(
            "".join(timestamp_match.groups()),
            "%Y%m%d%H%M%S",
        )
    else:
        started = dt.datetime.fromtimestamp(os.path.getmtime(path))

    profile = last_row.get("profile") or (
        "Clash 代理（国际）"
        if "clash_proxy" in lower_name
        else "直连国内"
        if "direct" in lower_name
        else "未知"
    )
    test_kind = last_row.get("test_kind") or (
        "仅上传"
        if "upload_only" in lower_name
        else "仅下载"
        if "download_only" in lower_name
        else "下载 + 上传同时"
    )
    source = last_row.get("proxy_source") or (
        "东京（A573）" if "tokyo" in lower_name else "—"
    )
    reported_web_p95 = optional_float(last_row.get("web_ttfb_p95_ms"))
    target_web_p95_values = [
        float(value)
        for value in re.findall(
            r":([0-9]+(?:\.[0-9]+)?)ms/",
            last_row.get("web_target_stats") or "",
        )
    ]
    if target_web_p95_values:
        reported_web_p95 = max(target_web_p95_values)

    return {
        "path": os.path.abspath(path),
        "filename": filename,
        "started": started.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile,
        "test_kind": test_kind,
        "source": source,
        "download_avg": optional_float(last_row.get("avg_mbps")),
        "upload_avg": optional_float(last_row.get("upload_avg_mbps")),
        "latency_p95": percentile(latency_samples, 0.95),
        "web_p95": (
            reported_web_p95
            if reported_web_p95 is not None
            else percentile(web_samples, 0.95)
        ),
        "web_success": optional_float(last_row.get("web_success_rate")),
        "download_errors": int(optional_float(last_row.get("download_errors")) or 0),
        "upload_errors": int(optional_float(last_row.get("upload_errors")) or 0),
        "region": last_row.get("ip_region") or "—",
        "duration": optional_float(last_row.get("elapsed_sec")),
    }


@dataclass
class UploadState:
    uploaded_bytes: int = 0
    request_count: int = 0
    errors: int = 0
    active_uploads: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_bytes(self, count: int) -> None:
        with self.lock:
            self.uploaded_bytes += count

    def add_request(self) -> None:
        with self.lock:
            self.request_count += 1

    def add_error(self) -> None:
        with self.lock:
            self.errors += 1

    def set_active_delta(self, delta: int) -> None:
        with self.lock:
            self.active_uploads += delta

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "uploaded_bytes": self.uploaded_bytes,
                "request_count": self.request_count,
                "errors": self.errors,
                "active_uploads": self.active_uploads,
            }


@dataclass
class WebExperienceState:
    samples: deque[dict] = field(default_factory=lambda: deque(maxlen=2400))
    request_count: int = 0
    failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_sample(
        self,
        target: str,
        ttfb_ms: Optional[float],
        total_ms: Optional[float],
        status: Optional[int],
        error: str = "",
    ) -> None:
        success = ttfb_ms is not None and status is not None and status < 500
        with self.lock:
            self.request_count += 1
            if not success:
                self.failures += 1
            self.samples.append(
                {
                    "target": target,
                    "ttfb_ms": ttfb_ms,
                    "total_ms": total_ms,
                    "status": status,
                    "success": success,
                    "error": error,
                }
            )

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "samples": list(self.samples),
                "request_count": self.request_count,
                "failures": self.failures,
            }


def web_probe_worker(
    target: str,
    url: str,
    state: WebExperienceState,
    stop: threading.Event,
    direct: bool,
    initial_delay: float,
    interval: float = 2.0,
) -> None:
    if stop.wait(initial_delay):
        return
    opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if direct
        else urllib.request.build_opener()
    )
    while not stop.is_set():
        separator = "&" if "?" in url else "?"
        probe_url = f"{url}{separator}_probe={time.time_ns()}"
        request = urllib.request.Request(
            probe_url,
            headers={
                "User-Agent": "Mozilla/5.0 net-stability-web-experience/1.0",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
                "Accept": "*/*",
            },
        )
        started = time.perf_counter()
        try:
            with opener.open(request, timeout=12.0) as response:
                response.read(1)
                ttfb_ms = (time.perf_counter() - started) * 1000
                response.read(64 * 1024)
                total_ms = (time.perf_counter() - started) * 1000
                status = int(response.getcode() or 0)
            state.add_sample(target, ttfb_ms, total_ms, status)
        except urllib.error.HTTPError as exc:
            # A 4xx response still proves that proxy, TLS and the website edge
            # responded; this is useful for sites protected by bot challenges.
            elapsed_ms = (time.perf_counter() - started) * 1000
            state.add_sample(
                target,
                elapsed_ms if exc.code < 500 else None,
                elapsed_ms,
                int(exc.code),
                f"HTTP {exc.code}",
            )
        except Exception as exc:
            state.add_sample(
                target,
                None,
                None,
                None,
                type(exc).__name__,
            )
        stop.wait(interval)


def upload_worker(
    worker_id: int,
    state: UploadState,
    stop: threading.Event,
    payload_bytes: int,
    timeout: float,
    chunk_size: int,
    host: str,
    port: int,
    path: str,
    use_system_proxy: bool,
) -> None:
    chunk = b"0" * chunk_size
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(payload_bytes),
        "User-Agent": f"net-stability-test/1.2 upload-{worker_id}",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }

    # Offset workers so their request/response pauses do not align and create
    # misleading all-zero intervals on high-latency proxy routes.
    if stop.wait((worker_id - 1) * 2.0):
        return

    while not stop.is_set():
        connection: Optional[http.client.HTTPSConnection] = None
        state.set_active_delta(1)
        try:
            if use_system_proxy:
                proxies = urllib.request.getproxies()
                proxy_url = proxies.get("https") or proxies.get("http")
                if not proxy_url:
                    raise RuntimeError("未检测到 Windows HTTP/HTTPS 代理")
                if "://" not in proxy_url:
                    proxy_url = f"http://{proxy_url}"
                parsed_proxy = urllib.parse.urlparse(proxy_url)
                if not parsed_proxy.hostname:
                    raise RuntimeError("系统代理地址无效")
                connection = http.client.HTTPSConnection(
                    parsed_proxy.hostname,
                    parsed_proxy.port or 80,
                    timeout=timeout,
                )
                connection.set_tunnel(host, port)
            else:
                connection = http.client.HTTPSConnection(host, port, timeout=timeout)

            host_header = host if port == 443 else f"{host}:{port}"
            while not stop.is_set():
                connection.putrequest("POST", path, skip_host=True)
                connection.putheader("Host", host_header)
                for name, value in headers.items():
                    connection.putheader(name, value)
                connection.endheaders()

                remaining = payload_bytes
                while remaining > 0 and not stop.is_set():
                    piece = chunk if remaining >= chunk_size else chunk[:remaining]
                    connection.send(piece)
                    state.add_bytes(len(piece))
                    remaining -= len(piece)

                if stop.is_set():
                    break
                response = connection.getresponse()
                response.read()
                if 200 <= response.status < 300:
                    state.add_request()
                else:
                    state.add_error()
                    break
        except Exception:
            state.add_error()
        finally:
            if connection is not None:
                connection.close()
            state.set_active_delta(-1)
        stop.wait(0.5)


def proxy_latency_worker(
    state: SharedState,
    stop: threading.Event,
    url_template: str,
    timeout: float,
    every: float,
) -> None:
    opener = urllib.request.build_opener()
    while not stop.is_set():
        started = time.perf_counter()
        url = url_template.format(cachebust=str(time.time_ns()))
        try:
            request = urllib.request.Request(
                url,
                headers={"User-Agent": "net-stability-test/1.3 latency"},
            )
            with opener.open(request, timeout=timeout) as response:
                response.read(1)
            state.add_latency((time.perf_counter() - started) * 1000)
        except Exception:
            state.add_latency(None)
        stop.wait(every)


class NetworkTestApp:
    SAMPLE_MS = 1000
    UPLOAD_SMOOTH_SECONDS = 15.0

    def __init__(self, root: tk.Tk, autostart: bool = False) -> None:
        self.root = root
        os.makedirs(LOG_DIR, exist_ok=True)
        self.root.title("网络稳定性实时测试（直连 / Clash 代理）")
        self.root.withdraw()
        dpi = float(self.root.winfo_fpixels("1i"))
        self._dpi_scale = max(1.0, min(3.0, dpi / 96.0))
        self.root.tk.call("tk", "scaling", dpi / 72.0)
        self.root.geometry(
            f"{self._scaled(1120)}x{self._scaled(780)}"
        )
        self.root.minsize(self._scaled(960), self._scaled(680))

        self._font_default = tkfont.nametofont("TkDefaultFont")
        system_family = self._font_default.actual("family")
        system_size = abs(int(self._font_default.actual("size"))) or 9
        self._font_small = tkfont.Font(
            root=self.root,
            family=system_family,
            size=max(8, system_size - 1),
        )
        self._font_heading = tkfont.Font(
            root=self.root,
            family=system_family,
            size=system_size + 1,
            weight="bold",
        )
        self._font_title = tkfont.Font(
            root=self.root,
            family=system_family,
            size=system_size + 5,
            weight="bold",
        )
        self._font_metric = tkfont.Font(
            root=self.root,
            family=system_family,
            size=system_size + 4,
            weight="bold",
        )
        self._font_metric_secondary = tkfont.Font(
            root=self.root,
            family=system_family,
            size=system_size + 1,
            weight="bold",
        )
        self._app_icon = None
        self._title_icon = None
        self._window_handles: dict[tk.Misc, int] = {}
        self._maximize_buttons: dict[tk.Misc, tk.Button] = {}
        self._resize_states: dict[tk.Misc, tuple] = {}
        self._resize_bound: set[tk.Misc] = set()
        self._drag_states: dict[tk.Misc, tuple[int, int, int, int]] = {}
        self._restore_geometries: dict[tk.Misc, str] = {}
        self._custom_maximized: set[tk.Misc] = set()
        try:
            if os.path.exists(APP_ICON_PNG):
                self._app_icon = tk.PhotoImage(file=APP_ICON_PNG)
                self.root.iconphoto(True, self._app_icon)
            if os.name == "nt" and os.path.exists(APP_ICON_ICO):
                self.root.iconbitmap(default=APP_ICON_ICO)
        except tk.TclError:
            # The app remains usable if a window manager cannot load an icon.
            pass

        self.state: Optional[SharedState] = None
        self.upload_state: Optional[UploadState] = None
        self.web_state: Optional[WebExperienceState] = None
        self.stop_event: Optional[threading.Event] = None
        self.workers: list[threading.Thread] = []
        self.running = False
        self.started = 0.0
        self.last_time = 0.0
        self.last_bytes = 0
        self.last_upload_bytes = 0
        self.upload_speed_window: deque[tuple[float, int]] = deque()
        self.last_latency_count = 0
        self.last_latency_failures = 0
        self.last_web_count = 0
        self.peak_mbps = 0.0
        self.upload_peak_mbps = 0.0
        self.samples: deque[
            tuple[float, float, float, Optional[float], Optional[float]]
        ] = deque(maxlen=300)
        self.csv_file = None
        self.csv_writer: Optional[csv.DictWriter] = None
        self.csv_path = ""
        self.after_id: Optional[str] = None
        self._status_animation_after: Optional[str] = None
        self._status_pulse_phase = 0.0
        self._advanced_popup_alpha_id: Optional[str] = None
        self.history_window: Optional[tk.Toplevel] = None
        self.history_tree: Optional[ttk.Treeview] = None
        self.history_paths: dict[str, str] = {}
        self.lookup_serial = 0
        self.public_ip = ""
        self.ip_region = ""
        self.ip_isp = ""

        self.duration_var = tk.StringVar(value="300")
        self.connections_var = tk.StringVar(value="8")
        self.upload_connections_var = tk.StringVar(value="4")
        self.profile_var = tk.StringVar(value=PROXY_PROFILE)
        self.test_kind_var = tk.StringVar(value=UPLOAD_ONLY)
        self.proxy_source_var = tk.StringVar(value=PROXY_SOURCE_TOKYO)
        self.web_profile_var = tk.StringVar(value=WEB_PROFILE_MIXED)
        self.web_interval_var = tk.StringVar(value="2")
        self.current_var = tk.StringVar(value="0.00 Mbps")
        self.average_var = tk.StringVar(value="0.00 Mbps")
        self.peak_var = tk.StringVar(value="0.00 Mbps")
        self.download_summary_var = tk.StringVar(
            value="0.00 MB/s"
        )
        self.upload_current_var = tk.StringVar(value="0.00 Mbps")
        self.upload_average_var = tk.StringVar(value="0.00 Mbps")
        self.upload_peak_var = tk.StringVar(value="0.00 Mbps")
        self.upload_summary_var = tk.StringVar(
            value="0.00 MB/s"
        )
        self.latency_var = tk.StringVar(value="-- ms")
        self.web_ttfb_var = tk.StringVar(value="-- ms")
        self.web_success_var = tk.StringVar(value="--")
        self.session_var = tk.StringVar(value="未开始")
        self.elapsed_var = tk.StringVar(value="0 秒")
        self.detail_var = tk.StringVar(value="等待开始")
        self.ip_info_var = tk.StringVar(value="出口 IP：查询中…")
        self.chart_title_var = tk.StringVar(value="实时趋势")
        self.chart_subtitle_var = tk.StringVar(value="最近 120 个采样 · 等待测试")
        self.mode_info_var = tk.StringVar(
            value=PROXY_SOURCES[PROXY_SOURCE_TOKYO]["info"]
        )
        self._ip_resize_after: Optional[str] = None
        self._ip_info_trace_id = self.ip_info_var.trace_add(
            "write",
            self._ip_info_changed,
        )
        self.theme_mode = load_ui_theme()
        self.sidebar_width = load_sidebar_width()
        self._sidebar_drag_origin: Optional[tuple[int, int]] = None
        self.chart_view_mode = "trend"
        self._chart_photo: Optional[ImageTk.PhotoImage] = None
        self._chart_resize_after: Optional[str] = None
        self.advanced_visible = False

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.overrideredirect(True)
        self.root.update_idletasks()
        self._center_window(self.root)
        self._apply_windows_appearance(self.root)
        self.root.deiconify()
        self.root.after(20, self._apply_windows_appearance, self.root)
        self.root.after(100, self._refresh_ip)
        if autostart:
            self.root.after(350, self.start)

    def _scaled(self, value: int | float) -> int:
        return max(1, round(value * self._dpi_scale))

    def _theme_palette(self) -> dict[str, str]:
        if self.theme_mode == "dark":
            return {
                "page": "#1f1f1d",
                "sidebar": "#252523",
                "surface": "#2b2b29",
                "surface_alt": "#30302e",
                "field": "#333333",
                "border": "#353533",
                "text": "#f4f4f4",
                "muted": "#b5b5b5",
                "subtle": "#8f8f8f",
                "button": "#323232",
                "button_hover": "#3b3b3b",
                "disabled": "#555555",
                "route": "#243647",
                "route_border": "#35536c",
                "route_text": "#cbd8e3",
                "grid": "#404040",
                "footer": "#292929",
                "selection": "#174f78",
                "tile": "#30302e",
                "tile_hover": "#393936",
                "tile_pressed": "#41413d",
                "tile_disabled": "#2a2a28",
            }
        return {
            "page": "#f7f7f5",
            "sidebar": "#f1f1ef",
            "surface": "#ffffff",
            "surface_alt": "#f7f7f5",
            "field": "#ffffff",
            "border": "#e9e9e5",
            "text": "#1f1f1f",
            "muted": "#666666",
            "subtle": "#7a7a7a",
            "button": "#f7f7f7",
            "button_hover": "#e9e9e9",
            "disabled": "#b7b7b7",
            "route": "#edf6ff",
            "route_border": "#d6e8f8",
            "route_text": "#4f5f6f",
            "grid": "#ececec",
            "footer": "#fafafa",
            "selection": "#d8eaff",
            "tile": "#e9e9e6",
            "tile_hover": "#dfdfdb",
            "tile_pressed": "#d5d5d0",
            "tile_disabled": "#eeeeeb",
        }

    def _build_ui(self) -> None:
        style = ttk.Style()
        s = self._scaled
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        palette = self._theme_palette()
        self._theme_colors = palette
        page_bg = palette["page"]
        panel_bg = palette["surface"]
        text_color = palette["text"]
        muted_color = palette["muted"]
        self.root.configure(background=page_bg)
        self.root.option_add("*TCombobox*Listbox.background", palette["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", text_color)
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#0067c0")
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        style.configure(
            "TButton",
            font=self._font_default,
            padding=(s(12), s(8)),
            relief="flat",
            borderwidth=0,
            background=palette["button"],
            foreground=text_color,
        )
        style.map(
            "TButton",
            background=[
                ("active", palette["button_hover"]),
                ("disabled", palette["button"]),
            ],
            foreground=[("disabled", palette["subtle"])],
        )
        style.configure(
            "TEntry",
            padding=s(7),
            relief="flat",
            borderwidth=0,
            fieldbackground=palette["field"],
            foreground=text_color,
            insertcolor=text_color,
            bordercolor=palette["field"],
            lightcolor=palette["field"],
            darkcolor=palette["field"],
        )
        style.configure(
            "TCombobox",
            padding=s(6),
            relief="flat",
            borderwidth=0,
            fieldbackground=palette["field"],
            background=palette["field"],
            foreground=text_color,
            bordercolor=palette["field"],
            lightcolor=palette["field"],
            darkcolor=palette["field"],
            arrowcolor=muted_color,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["field"])],
            selectbackground=[("readonly", palette["field"])],
            selectforeground=[("readonly", text_color)],
        )
        style.configure(
            "TSpinbox",
            padding=s(6),
            relief="flat",
            borderwidth=0,
            fieldbackground=palette["field"],
            background=palette["field"],
            foreground=text_color,
            arrowcolor=muted_color,
            bordercolor=palette["field"],
            lightcolor=palette["field"],
            darkcolor=palette["field"],
        )
        style.configure("App.TFrame", background=page_bg)
        style.configure("Panel.TFrame", background=panel_bg, relief="flat", borderwidth=0)
        style.configure("Card.TFrame", background=panel_bg)
        style.configure("Page.TLabel", background=page_bg, foreground=text_color)
        style.configure("Panel.TLabel", background=panel_bg, foreground=text_color)
        style.configure(
            "Title.TLabel",
            background=page_bg,
            foreground=text_color,
            font=self._font_title,
        )
        style.configure(
            "Subtitle.TLabel",
            background=page_bg,
            foreground=muted_color,
            font=self._font_small,
        )
        style.configure(
            "Section.TLabel",
            background=panel_bg,
            foreground=text_color,
            font=self._font_heading,
        )
        style.configure(
            "MetricName.TLabel",
            background=panel_bg,
            foreground=muted_color,
            font=self._font_small,
        )
        style.configure(
            "Metric.TLabel",
            background=panel_bg,
            foreground=text_color,
            font=self._font_metric_secondary,
        )
        style.configure(
            "PrimaryMetric.TLabel",
            background=panel_bg,
            foreground="#0067c0",
            font=self._font_metric,
        )
        style.configure(
            "UploadMetric.TLabel",
            background=panel_bg,
            foreground="#2e7d32",
            font=self._font_metric,
        )
        style.configure(
            "LatencyMetric.TLabel",
            background=panel_bg,
            foreground="#ef6c00",
            font=self._font_metric,
        )
        style.configure(
            "Accent.TButton",
            background="#0067c0",
            foreground="#ffffff",
            borderwidth=0,
            relief="flat",
            focuscolor="#0067c0",
            padding=(s(16), s(8)),
        )
        style.map(
            "Accent.TButton",
            background=[
                ("active", "#005a9e"),
                ("disabled", palette["disabled"]),
            ],
            foreground=[("disabled", "#f2f4f6")],
        )
        style.configure(
            "Stop.TButton",
            background=palette["button"],
            foreground="#c42b1c",
            borderwidth=0,
            relief="flat",
            padding=(s(16), s(8)),
        )
        style.map(
            "Stop.TButton",
            background=[
                ("active", "#5a2727" if self.theme_mode == "dark" else "#fde7e9")
            ],
        )
        style.configure(
            "History.Treeview",
            background=panel_bg,
            fieldbackground=panel_bg,
            foreground=text_color,
            rowheight=s(30),
            font=self._font_default,
        )
        style.configure(
            "History.Treeview.Heading",
            background=palette["surface_alt"],
            foreground=text_color,
            font=self._font_heading,
            padding=(s(6), s(8)),
        )
        style.map(
            "History.Treeview",
            background=[("selected", palette["selection"])],
            foreground=[("selected", text_color)],
        )

        self._build_dashboard_shell(
            page_bg=page_bg,
            panel_bg=panel_bg,
            text_color=text_color,
            muted_color=muted_color,
        )
        return

        self._build_titlebar(
            self.root,
            "网络稳定性测试",
            self.close,
        )

        outer = ttk.Frame(
            self.root,
            padding=(s(18), s(16), s(18), s(14)),
            style="App.TFrame",
        )
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill="x", pady=(0, s(12)))
        header.columnconfigure(0, weight=1)
        headline = ttk.Frame(header, style="App.TFrame")
        headline.grid(row=0, column=0, sticky="w")
        ttk.Label(headline, text="实时网络质量", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            headline,
            text="同时观察吞吐、连接延迟、网页响应和出口线路；测试报告会自动保存到历史记录。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(s(2), 0))

        actions = ttk.Frame(header, style="App.TFrame")
        actions.grid(row=0, column=1, sticky="e")
        self.history_button = ttk.Button(
            actions,
            text="测速历史",
            command=self.show_history,
            width=10,
        )
        self.history_button.pack(side="right")
        self.stop_button = ttk.Button(
            actions,
            text="停止",
            command=self.stop,
            state="disabled",
            style="Stop.TButton",
            width=9,
        )
        self.stop_button.pack(side="right", padx=s(8))
        self.start_button = ttk.Button(
            actions,
            text="开始测试",
            command=self.start,
            style="Accent.TButton",
            width=10,
        )
        self.start_button.pack(side="right")

        controls = ttk.Frame(
            outer,
            padding=(s(14), s(12)),
            style="Panel.TFrame",
        )
        controls.pack(fill="x")
        controls.columnconfigure(0, weight=1)

        settings_header = ttk.Frame(controls, style="Card.TFrame")
        settings_header.grid(row=0, column=0, sticky="ew", pady=(0, s(10)))
        ttk.Label(
            settings_header,
            text="测试设置",
            style="Section.TLabel",
        ).pack(side="left")
        ttk.Label(
            settings_header,
            text="上传实时速度采用 15 秒平滑",
            style="MetricName.TLabel",
        ).pack(side="right")

        settings = ttk.Frame(controls, style="Card.TFrame")
        settings.grid(row=1, column=0, sticky="nsew")
        for column in range(4):
            settings.columnconfigure(column, weight=1, uniform="setting")

        def field_frame(column: int, row: int, label: str) -> ttk.Frame:
            frame = ttk.Frame(settings, style="Card.TFrame")
            frame.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=(0 if column == 0 else s(8), s(8) if column < 3 else 0),
                pady=(0 if row == 0 else s(10), 0),
            )
            ttk.Label(frame, text=label, style="MetricName.TLabel").pack(
                anchor="w", pady=(0, s(4))
            )
            return frame

        profile_field = field_frame(0, 0, "网络模式")
        self.profile_box = ttk.Combobox(
            profile_field,
            textvariable=self.profile_var,
            values=(DIRECT_PROFILE, PROXY_PROFILE),
            state="readonly",
        )
        self.profile_box.pack(fill="x")
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_changed)

        test_field = field_frame(1, 0, "测试类型")
        self.test_kind_box = ttk.Combobox(
            test_field,
            textvariable=self.test_kind_var,
            values=(DOWNLOAD_ONLY, UPLOAD_ONLY, DUPLEX_TEST, WEB_ONLY),
            state="readonly",
        )
        self.test_kind_box.pack(fill="x")

        source_field = field_frame(2, 0, "代理测速源")
        self.source_box = ttk.Combobox(
            source_field,
            textvariable=self.proxy_source_var,
            values=(
                PROXY_SOURCE_TOKYO,
                PROXY_SOURCE_LA,
                PROXY_SOURCE_FRANKFURT,
            ),
            state="readonly",
        )
        self.source_box.pack(fill="x")
        self.source_box.bind("<<ComboboxSelected>>", self._source_changed)

        web_profile_field = field_frame(3, 0, "网页体验目标")
        self.web_profile_box = ttk.Combobox(
            web_profile_field,
            textvariable=self.web_profile_var,
            values=(
                WEB_PROFILE_MIXED,
                WEB_PROFILE_CHATGPT,
                WEB_PROFILE_COMMON,
                WEB_PROFILE_OFF,
            ),
            state="readonly",
        )
        self.web_profile_box.pack(fill="x")

        duration_field = field_frame(0, 1, "测试时长（秒）")
        ttk.Entry(duration_field, textvariable=self.duration_var).pack(fill="x")
        download_field = field_frame(1, 1, "下载并发")
        ttk.Spinbox(
            download_field,
            from_=1,
            to=32,
            textvariable=self.connections_var,
        ).pack(fill="x")
        upload_field = field_frame(2, 1, "上传并发")
        ttk.Spinbox(
            upload_field,
            from_=1,
            to=16,
            textvariable=self.upload_connections_var,
        ).pack(fill="x")
        web_interval_field = field_frame(3, 1, "网页采样间隔（秒）")
        self.web_interval_box = ttk.Spinbox(
            web_interval_field,
            from_=1,
            to=10,
            increment=1,
            textvariable=self.web_interval_var,
        )
        self.web_interval_box.pack(fill="x")

        info_panel = ttk.Frame(
            outer,
            padding=(s(14), s(10)),
            style="Panel.TFrame",
        )
        info_panel.pack(fill="x", pady=(s(10), s(10)))
        info_panel.columnconfigure(1, weight=1)
        ttk.Label(
            info_panel,
            text="●  线路",
            foreground="#ef6c00",
            style="MetricName.TLabel",
        ).grid(
            row=0,
            column=0,
            sticky="nw",
            padx=(0, s(12)),
            pady=(s(1), s(5)),
        )
        self.mode_info_label = ttk.Label(
            info_panel,
            textvariable=self.mode_info_var,
            foreground="#a55a00",
            background=panel_bg,
            justify="left",
            wraplength=s(930),
        )
        self.mode_info_label.grid(
            row=0,
            column=1,
            sticky="ew",
            pady=(s(1), s(5)),
        )
        ttk.Label(
            info_panel,
            text="●  出口",
            foreground="#1976d2",
            style="MetricName.TLabel",
        ).grid(
            row=1,
            column=0,
            sticky="nw",
            padx=(0, s(12)),
            pady=(s(5), s(1)),
        )
        self.ip_info_label = ttk.Label(
            info_panel,
            textvariable=self.ip_info_var,
            foreground="#1769aa",
            background=panel_bg,
            font=self._font_heading,
            justify="left",
            wraplength=s(930),
        )
        self.ip_info_label.grid(
            row=1,
            column=1,
            sticky="ew",
            pady=(s(5), s(1)),
        )

        metrics = ttk.Frame(outer, style="App.TFrame")
        metrics.pack(fill="x", pady=(0, s(10)))
        for column in range(3):
            metrics.columnconfigure(column, weight=1, uniform="metric")

        def metric_card(column: int, title: str, accent: str) -> ttk.Frame:
            card = ttk.Frame(
                metrics,
                padding=(s(14), s(11)),
                style="Panel.TFrame",
            )
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(
                    0 if column == 0 else s(5),
                    0 if column == 2 else s(5),
                ),
            )
            tk.Frame(card, background=accent, height=s(3)).pack(
                fill="x", pady=(0, s(8))
            )
            ttk.Label(card, text=title, style="Section.TLabel").pack(anchor="w")
            return card

        def metric_row(
            parent: ttk.Frame,
            name: str,
            variable: tk.StringVar,
            value_style: str = "Metric.TLabel",
            primary: bool = False,
        ) -> None:
            ttk.Label(parent, text=name, style="MetricName.TLabel").pack(
                anchor="w",
                pady=((s(10) if primary else s(7)), s(1)),
            )
            ttk.Label(parent, textvariable=variable, style=value_style).pack(anchor="w")

        download_card = metric_card(0, "下载速度", "#1976d2")
        metric_row(
            download_card,
            "当前",
            self.current_var,
            "PrimaryMetric.TLabel",
            primary=True,
        )
        metric_row(download_card, "平均", self.average_var)
        metric_row(download_card, "峰值", self.peak_var)

        upload_card = metric_card(1, "上传速度", "#2e7d32")
        metric_row(
            upload_card,
            "当前（15 秒平滑）",
            self.upload_current_var,
            "UploadMetric.TLabel",
            primary=True,
        )
        metric_row(upload_card, "平均", self.upload_average_var)
        metric_row(upload_card, "峰值", self.upload_peak_var)

        connection_card = metric_card(2, "连接状态", "#ef6c00")
        metric_row(
            connection_card,
            "当前延迟",
            self.latency_var,
            "LatencyMetric.TLabel",
            primary=True,
        )
        metric_row(connection_card, "最慢网页 P95 / 抖动", self.web_ttfb_var)
        metric_row(connection_card, "网页响应率", self.web_success_var)

        chart_header = ttk.Frame(outer, style="App.TFrame")
        chart_header.pack(fill="x", pady=(s(2), s(6)))
        ttk.Label(
            chart_header,
            text="实时趋势",
            style="Page.TLabel",
            font=self._font_heading,
        ).pack(side="left")
        legend = ttk.Frame(chart_header, style="App.TFrame")
        legend.pack(side="right")
        ttk.Label(legend, text="● 下载", foreground="#1976d2", style="Page.TLabel").pack(side="left")
        ttk.Label(legend, text="● 上传", foreground="#2e7d32", style="Page.TLabel").pack(side="left", padx=s(12))
        ttk.Label(legend, text="● 延迟", foreground="#ef6c00", style="Page.TLabel").pack(side="left")
        ttk.Label(legend, text="● 网页", foreground="#7b1fa2", style="Page.TLabel").pack(side="left", padx=(s(12), 0))

        self.chart = tk.Canvas(
            outer,
            background=panel_bg,
            height=s(180),
            highlightthickness=0,
        )
        self.chart.pack(fill="both", expand=True)
        self.chart.bind("<Configure>", self._schedule_chart_redraw)

        self.detail_label = ttk.Label(
            outer,
            textvariable=self.detail_var,
            style="Subtitle.TLabel",
            justify="left",
            wraplength=1120,
        )
        self.detail_label.pack(fill="x", pady=(s(8), 0))
        self._last_wrap_width = 0
        self.root.bind("<Configure>", self._on_root_resize, add="+")

    def _build_dashboard_shell(
        self,
        page_bg: str,
        panel_bg: str,
        text_color: str,
        muted_color: str,
    ) -> None:
        """Build the compact Fluent-style main dashboard."""
        s = self._scaled
        palette = self._theme_colors
        sidebar_bg = palette["sidebar"]
        border = palette["border"]
        accent = "#0067c0"
        field_bg = palette["field"]
        route_bg = palette["route"]
        route_border = palette["route_border"]
        route_text = palette["route_text"]
        icon_tints = (
            ("#173a55", "#163c22", "#512d20", "#3c2c55")
            if self.theme_mode == "dark"
            else ("#e8f3fb", "#e9f5e9", "#fff0e8", "#f3edfb")
        )

        self._build_titlebar(self.root, "网络稳定性测试", self.close)

        shell = tk.Frame(self.root, background=page_bg)
        shell.pack(fill="both", expand=True)
        shell.grid_rowconfigure(0, weight=1)
        shell.grid_columnconfigure(1, weight=1)

        sidebar = tk.Frame(
            shell,
            width=s(self.sidebar_width),
            background=sidebar_bg,
            highlightbackground=border,
            highlightthickness=s(1),
        )
        self.sidebar = sidebar
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_propagate(False)
        sidebar.pack_propagate(False)

        sidebar_inner = tk.Frame(sidebar, background=sidebar_bg)
        sidebar_inner.pack(
            fill="both",
            expand=True,
            padx=s(16),
            pady=(s(18), s(16)),
        )
        self.sidebar_resize_handle = tk.Frame(
            sidebar,
            background=border,
            width=s(6),
            cursor="sb_h_double_arrow",
        )
        self.sidebar_resize_handle.place(
            relx=1.0,
            y=0,
            relheight=1.0,
            width=s(6),
            anchor="ne",
        )
        self.sidebar_resize_handle.bind(
            "<Enter>",
            lambda _event: self.sidebar_resize_handle.configure(
                background=accent
            ),
        )
        self.sidebar_resize_handle.bind(
            "<Leave>",
            lambda _event: self.sidebar_resize_handle.configure(
                background=border
            ),
        )
        self.sidebar_resize_handle.bind(
            "<ButtonPress-1>",
            self._begin_sidebar_resize,
        )
        self.sidebar_resize_handle.bind(
            "<B1-Motion>",
            self._resize_sidebar,
        )
        self.sidebar_resize_handle.bind(
            "<ButtonRelease-1>",
            self._end_sidebar_resize,
        )

        config_area = tk.Frame(sidebar_inner, background=sidebar_bg)
        config_area.pack(fill="x")
        tk.Label(
            config_area,
            text="测试配置",
            background=sidebar_bg,
            foreground=text_color,
            font=self._font_title,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            config_area,
            text="选择线路、负载和网页目标",
            background=sidebar_bg,
            foreground=muted_color,
            font=self._font_small,
            anchor="w",
        ).pack(fill="x", pady=(s(2), s(14)))

        def field(label: str) -> tk.Frame:
            frame = tk.Frame(config_area, background=sidebar_bg)
            frame.pack(fill="x", pady=(0, s(10)))
            tk.Label(
                frame,
                text=label,
                background=sidebar_bg,
                foreground=muted_color,
                font=self._font_small,
                anchor="w",
            ).pack(fill="x", pady=(0, s(4)))
            return frame

        def control_surface(parent: tk.Misc, height: int = 36) -> tk.Frame:
            panel = RoundedPanel(
                parent,
                parent_background=sidebar_bg,
                fill=field_bg,
                border=border,
                radius=s(12),
                padding=s(4),
                fixed_height=s(height),
            )
            panel.pack(fill="x")
            return panel.inner

        def rounded_button(
            parent: tk.Misc,
            *,
            parent_background: str,
            fill: str,
            border_color: str,
            text: str,
            command,
            style_name: str = "TButton",
            state: str = "normal",
            width: int = 86,
            height: int = 38,
        ) -> tuple[AnimatedRoundedButton, AnimatedRoundedButton]:
            if style_name == "Accent.TButton":
                foreground = "#ffffff"
                hover_fill = "#147dcc"
                pressed_fill = "#005a9e"
            elif style_name == "Stop.TButton":
                foreground = "#ff8a84" if self.theme_mode == "dark" else "#c42b1c"
                hover_fill = palette["tile_hover"]
                pressed_fill = palette["tile_pressed"]
            else:
                foreground = text_color
                hover_fill = palette["tile_hover"]
                pressed_fill = palette["tile_pressed"]
            button = AnimatedRoundedButton(
                parent,
                text=text,
                command=command,
                state=state,
                width=s(width),
                height=s(height),
                radius=s(13),
                parent_background=parent_background,
                fill=fill,
                hover_fill=hover_fill,
                pressed_fill=pressed_fill,
                disabled_fill=palette["tile_disabled"],
                foreground=foreground,
                disabled_foreground=palette["subtle"],
                font=self._font_default,
            )
            return button, button

        self.profile_box = AnimatedSelectTile(
            config_area,
            title="网络模式",
            icon="◉",
            variable=self.profile_var,
            values=(DIRECT_PROFILE, PROXY_PROFILE),
            width=s(248),
            height=s(56),
            radius=s(17),
            palette=palette,
            font=self._font_default,
            small_font=self._font_small,
            accent=accent,
            state="readonly",
        )
        self.profile_box.pack(fill="x", pady=(0, s(8)))
        self.profile_box.bind("<<ComboboxSelected>>", self._profile_changed)

        self.test_kind_box = AnimatedSelectTile(
            config_area,
            title="测试类型",
            icon="↯",
            variable=self.test_kind_var,
            values=(DOWNLOAD_ONLY, UPLOAD_ONLY, DUPLEX_TEST, WEB_ONLY),
            width=s(248),
            height=s(56),
            radius=s(17),
            palette=palette,
            font=self._font_default,
            small_font=self._font_small,
            accent=accent,
            state="readonly",
        )
        self.test_kind_box.pack(fill="x", pady=(0, s(8)))
        self.test_kind_box.bind(
            "<<ComboboxSelected>>",
            self._test_kind_changed,
        )

        self.source_box = AnimatedSelectTile(
            config_area,
            title="代理测速源",
            icon="◎",
            variable=self.proxy_source_var,
            values=(
                PROXY_SOURCE_TOKYO,
                PROXY_SOURCE_LA,
                PROXY_SOURCE_FRANKFURT,
            ),
            width=s(248),
            height=s(56),
            radius=s(17),
            palette=palette,
            font=self._font_default,
            small_font=self._font_small,
            accent=accent,
            state="readonly",
        )
        self.source_box.pack(fill="x", pady=(0, s(8)))
        self.source_box.bind("<<ComboboxSelected>>", self._source_changed)

        self.web_profile_box = AnimatedSelectTile(
            config_area,
            title="网页体验目标",
            icon="◌",
            variable=self.web_profile_var,
            values=(
                WEB_PROFILE_MIXED,
                WEB_PROFILE_CHATGPT,
                WEB_PROFILE_COMMON,
                WEB_PROFILE_OFF,
            ),
            width=s(248),
            height=s(56),
            radius=s(17),
            palette=palette,
            font=self._font_default,
            small_font=self._font_small,
            accent=accent,
            state="readonly",
        )
        self.web_profile_box.pack(fill="x", pady=(0, s(8)))
        self.web_profile_box.bind(
            "<<ComboboxSelected>>",
            self._web_profile_changed,
        )

        self.duration_tile = RoundedEntryTile(
            config_area,
            title="测试时长",
            icon="◷",
            variable=self.duration_var,
            suffix="秒",
            width=s(248),
            height=s(56),
            radius=s(17),
            palette=palette,
            font=self._font_default,
            small_font=self._font_small,
            accent=accent,
        )
        self.duration_tile.pack(fill="x", pady=(0, s(8)))
        self.duration_entry = self.duration_tile.entry

        self.advanced_toggle_button = AnimatedRoundedButton(
            config_area,
            text="高级参数",
            command=self._toggle_advanced,
            width=s(248),
            height=s(36),
            radius=s(13),
            parent_background=sidebar_bg,
            fill=sidebar_bg,
            hover_fill=palette["tile_hover"],
            pressed_fill=palette["tile_pressed"],
            disabled_fill=palette["tile_disabled"],
            foreground=text_color,
            disabled_foreground=palette["subtle"],
            font=self._font_default,
            alignment="left",
            leading_icon="⚙",
            trailing_text="›",
        )
        self.advanced_toggle_button.pack(fill="x", pady=(0, s(4)))

        self.advanced_popup = tk.Toplevel(self.root)
        self.advanced_popup.withdraw()
        self.advanced_popup.overrideredirect(True)
        self.advanced_popup.transient(self.root)
        popup_key = "#010203"
        self.advanced_popup.configure(background=popup_key)
        try:
            self.advanced_popup.wm_attributes("-transparentcolor", popup_key)
        except tk.TclError:
            self.advanced_popup.configure(background=panel_bg)
        advanced_surface = RoundedPanel(
            self.advanced_popup,
            parent_background=popup_key,
            fill=panel_bg,
            border=border,
            radius=s(22),
            padding=s(8),
            fixed_height=s(226),
        )
        advanced_surface.pack(fill="both", expand=True)
        advanced_body = advanced_surface.inner
        advanced_header = tk.Frame(advanced_body, background=panel_bg)
        advanced_header.pack(fill="x", padx=s(10), pady=(s(7), s(6)))
        advanced_heading = tk.Frame(advanced_header, background=panel_bg)
        advanced_heading.pack(side="left", fill="x", expand=True)
        tk.Label(
            advanced_heading,
            text="高级参数",
            background=panel_bg,
            foreground=text_color,
            font=self._font_heading,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            advanced_heading,
            text="调整并发负载与网页采样频率",
            background=panel_bg,
            foreground=muted_color,
            font=self._font_small,
            anchor="w",
        ).pack(fill="x", pady=(s(1), 0))
        close_advanced = AnimatedRoundedButton(
            advanced_header,
            text="×",
            command=self._hide_advanced_popup,
            width=s(30),
            height=s(30),
            radius=s(12),
            parent_background=panel_bg,
            fill=panel_bg,
            hover_fill=palette["tile_hover"],
            pressed_fill=palette["tile_pressed"],
            disabled_fill=palette["tile_disabled"],
            foreground=muted_color,
            disabled_foreground=palette["subtle"],
            font=self._font_heading,
        )
        close_advanced.pack(side="right", padx=(s(6), 0))

        advanced_fields = tk.Frame(advanced_body, background=panel_bg)
        advanced_fields.pack(fill="both", expand=True, padx=s(4))

        def advanced_entry(
            title: str,
            icon: str,
            variable: tk.StringVar,
            suffix: str,
        ) -> tk.Entry:
            tile = RoundedEntryTile(
                advanced_fields,
                title=title,
                icon=icon,
                variable=variable,
                suffix=suffix,
                width=s(214),
                height=s(48),
                radius=s(15),
                palette=palette,
                font=self._font_default,
                small_font=self._font_small,
                accent=accent,
                parent_background=panel_bg,
            )
            tile.pack(fill="x", pady=(0, s(5)))
            return tile.entry

        self.download_connections_box = advanced_entry(
            "下载并发连接",
            "↓",
            self.connections_var,
            "路",
        )
        self.upload_connections_box = advanced_entry(
            "上传并发连接",
            "↑",
            self.upload_connections_var,
            "路",
        )
        self.web_interval_box = advanced_entry(
            "网页采样间隔",
            "◷",
            self.web_interval_var,
            "秒",
        )
        self.advanced_popup.bind("<Escape>", lambda _event: self._hide_advanced_popup())
        self.advanced_popup.bind("<FocusOut>", self._advanced_popup_focus_out, add="+")

        sidebar_footer = tk.Frame(sidebar_inner, background=sidebar_bg)
        sidebar_footer.pack(fill="x", side="bottom")

        route_note_panel = RoundedPanel(
            sidebar_footer,
            parent_background=sidebar_bg,
            fill=route_bg,
            border=route_border,
            radius=s(18),
            padding=s(7),
            fixed_height=s(156),
        )
        self.route_note_panel = route_note_panel
        route_note_panel.pack(fill="x", pady=(s(10), s(12)))
        route_note = route_note_panel.inner
        self.route_note_title_label = tk.Label(
            route_note,
            text="线路说明",
            background=route_bg,
            foreground=accent,
            font=self._font_heading,
            anchor="w",
        )
        self.route_note_title_label.pack(
            fill="x",
            padx=s(11),
            pady=(s(9), s(2)),
        )
        self.mode_info_label = tk.Label(
            route_note,
            textvariable=self.mode_info_var,
            background=route_bg,
            foreground=route_text,
            font=self._font_default,
            justify="left",
            anchor="w",
            wraplength=s(max(120, self.sidebar_width - 68)),
            pady=s(2),
        )
        self.mode_info_label.pack(
            fill="x",
            padx=s(11),
            pady=(0, s(10)),
        )

        action_row = tk.Frame(sidebar_footer, background=sidebar_bg)
        action_row.pack(fill="x")
        action_row.grid_columnconfigure(0, weight=2)
        action_row.grid_columnconfigure(1, weight=1)
        start_panel, self.start_button = rounded_button(
            action_row,
            parent_background=sidebar_bg,
            fill="#0067c0",
            border_color="#0067c0",
            text="开始测试",
            command=self.start,
            style_name="Accent.TButton",
            width=124,
        )
        start_panel.grid(row=0, column=0, sticky="ew", padx=(0, s(6)))
        stop_panel, self.stop_button = rounded_button(
            action_row,
            parent_background=sidebar_bg,
            fill=palette["button"],
            border_color=border,
            text="停止",
            command=self.stop,
            state="disabled",
            style_name="Stop.TButton",
            width=72,
        )
        stop_panel.grid(row=0, column=1, sticky="ew")

        content = tk.Frame(shell, background=page_bg)
        content.grid(row=0, column=1, sticky="nsew", padx=s(20), pady=s(18))

        header = tk.Frame(content, background=page_bg)
        header.pack(fill="x", pady=(0, s(14)))
        headline = tk.Frame(header, background=page_bg)
        headline.pack(side="left", fill="x", expand=True)
        tk.Label(
            headline,
            text="网络质量概览",
            background=page_bg,
            foreground=text_color,
            font=self._font_title,
            anchor="w",
        ).pack(anchor="w")
        status_line = tk.Frame(headline, background=page_bg)
        status_line.pack(anchor="w", pady=(s(3), 0))
        self.status_dot_label = tk.Label(
            status_line,
            text="●",
            background=page_bg,
            foreground=palette["subtle"],
            font=self._font_small,
        )
        self.status_dot_label.pack(side="left")
        tk.Label(
            status_line,
            textvariable=self.session_var,
            background=page_bg,
            foreground=muted_color,
            font=self._font_small,
        ).pack(side="left", padx=(s(5), 0))
        tk.Label(
            status_line,
            text="·",
            background=page_bg,
            foreground=palette["subtle"],
            font=self._font_small,
        ).pack(side="left", padx=s(6))
        tk.Label(
            status_line,
            textvariable=self.elapsed_var,
            background=page_bg,
            foreground=muted_color,
            font=self._font_small,
        ).pack(side="left")

        header_actions = tk.Frame(header, background=page_bg)
        header_actions.pack(side="right")
        theme_panel, self.theme_button = rounded_button(
            header_actions,
            parent_background=page_bg,
            fill=palette["button"],
            border_color=border,
            text="☀ 亮色" if self.theme_mode == "dark" else "☾ 暗色",
            command=self._toggle_theme,
            width=78,
            height=34,
        )
        theme_panel.pack(side="left", padx=(0, s(8)))
        refresh_panel, _refresh_button = rounded_button(
            header_actions,
            parent_background=page_bg,
            fill=palette["button"],
            border_color=border,
            text="刷新出口",
            command=self._refresh_ip,
            width=80,
            height=34,
        )
        refresh_panel.pack(side="left", padx=(0, s(8)))
        history_panel, self.history_button = rounded_button(
            header_actions,
            parent_background=page_bg,
            fill=palette["button"],
            border_color=border,
            text="历史记录",
            command=self.show_history,
            width=80,
            height=34,
        )
        history_panel.pack(side="left")

        route_panel = RoundedPanel(
            content,
            parent_background=page_bg,
            fill=panel_bg,
            border=border,
            radius=s(18),
            padding=s(6),
            fixed_height=s(52),
        )
        self.ip_route_panel = route_panel
        route_panel.pack(fill="x", pady=(0, s(12)))
        route_strip = route_panel.inner
        self.ip_route_icon_label = tk.Label(
            route_strip,
            text="◎",
            background=panel_bg,
            foreground=accent,
            font=self._font_heading,
            pady=s(2),
        )
        self.ip_route_icon_label.pack(
            side="left",
            padx=(s(13), s(9)),
            pady=s(5),
        )
        self.ip_info_label = tk.Label(
            route_strip,
            textvariable=self.ip_info_var,
            background=panel_bg,
            foreground=text_color,
            font=self._font_default,
            justify="left",
            anchor="w",
            wraplength=s(680),
            pady=s(2),
        )
        self.ip_info_label.pack(
            side="left",
            fill="x",
            expand=True,
            pady=s(5),
            padx=(0, s(12)),
        )

        metrics = tk.Frame(content, background=page_bg)
        metrics.pack(fill="x", pady=(0, s(12)))
        for column in range(4):
            metrics.grid_columnconfigure(column, weight=1, uniform="metric")

        def metric_card(
            column: int,
            icon: str,
            title: str,
            color: str,
            tint: str,
            primary_var: tk.StringVar,
            caption: str,
            secondary_var: tk.StringVar,
            primary_font: Optional[tkfont.Font] = None,
        ) -> None:
            card = RoundedPanel(
                metrics,
                parent_background=page_bg,
                fill=panel_bg,
                border=border,
                radius=s(20),
                padding=s(7),
                fixed_height=s(128),
            )
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0 if column == 0 else s(5), 0),
            )
            card_body = card.inner
            head = tk.Frame(card_body, background=panel_bg)
            head.pack(fill="x", padx=s(12), pady=(s(11), s(4)))
            tk.Label(
                head,
                text=icon,
                width=2,
                background=tint,
                foreground=color,
                font=self._font_heading,
            ).pack(side="left")
            tk.Label(
                head,
                text=title,
                background=panel_bg,
                foreground=text_color,
                font=self._font_default,
            ).pack(side="left", padx=(s(7), 0))
            tk.Label(
                card_body,
                textvariable=primary_var,
                background=panel_bg,
                foreground=color,
                font=primary_font or self._font_metric,
                anchor="w",
            ).pack(fill="x", padx=s(13), pady=(s(1), s(4)))
            bottom = tk.Frame(card_body, background=panel_bg)
            bottom.pack(fill="x", padx=s(13))
            tk.Label(
                bottom,
                text=caption,
                background=panel_bg,
                foreground=muted_color,
                font=self._font_small,
            ).pack(side="left")
            tk.Label(
                bottom,
                textvariable=secondary_var,
                background=panel_bg,
                foreground=text_color,
                font=self._font_small,
                anchor="e",
            ).pack(side="right")

        metric_card(
            0,
            "↓",
            "下载",
            "#0067c0",
            icon_tints[0],
            self.current_var,
            "当前换算",
            self.download_summary_var,
        )
        metric_card(
            1,
            "↑",
            "上传",
            "#107c10",
            icon_tints[1],
            self.upload_current_var,
            "当前换算",
            self.upload_summary_var,
        )
        metric_card(
            2,
            "↔",
            "连接延迟",
            "#d83b01",
            icon_tints[2],
            self.latency_var,
            "测试状态",
            self.session_var,
        )
        metric_card(
            3,
            "◎",
            "最慢网页 P95 / 抖动",
            "#744da9",
            icon_tints[3],
            self.web_ttfb_var,
            "响应率",
            self.web_success_var,
            self._font_metric_secondary,
        )

        chart_panel = RoundedPanel(
            content,
            parent_background=page_bg,
            fill=panel_bg,
            border=border,
            radius=s(24),
            padding=s(9),
        )
        chart_panel.pack(fill="both", expand=True)
        chart_card = chart_panel.inner
        chart_header = tk.Frame(chart_card, background=panel_bg)
        chart_header.pack(fill="x", padx=s(14), pady=(s(11), s(8)))
        chart_heading = tk.Frame(chart_header, background=panel_bg)
        chart_heading.pack(side="left", fill="x", expand=True)
        tk.Label(
            chart_heading,
            textvariable=self.chart_title_var,
            background=panel_bg,
            foreground=text_color,
            font=self._font_heading,
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            chart_heading,
            textvariable=self.chart_subtitle_var,
            background=panel_bg,
            foreground=muted_color,
            font=self._font_small,
            anchor="w",
        ).pack(fill="x", pady=(s(2), 0))
        chart_tools = tk.Frame(chart_header, background=panel_bg)
        chart_tools.pack(side="right")
        view_tabs = tk.Frame(chart_tools, background=panel_bg)
        view_tabs.pack(side="left", padx=(0, s(8)))
        self.trend_tab_button = AnimatedRoundedButton(
            view_tabs,
            text="趋势",
            command=lambda: self._set_chart_view("trend"),
            width=s(50),
            height=s(28),
            radius=s(11),
            parent_background=panel_bg,
            fill=panel_bg,
            hover_fill=palette["tile_hover"],
            pressed_fill=palette["tile_pressed"],
            disabled_fill=palette["tile_disabled"],
            foreground=text_color,
            disabled_foreground=palette["subtle"],
            font=self._font_small,
        )
        self.trend_tab_button.pack(side="left")
        self.web_tab_button = AnimatedRoundedButton(
            view_tabs,
            text="网页分析",
            command=lambda: self._set_chart_view("web"),
            width=s(70),
            height=s(28),
            radius=s(11),
            parent_background=panel_bg,
            fill=panel_bg,
            hover_fill=palette["tile_hover"],
            pressed_fill=palette["tile_pressed"],
            disabled_fill=palette["tile_disabled"],
            foreground=text_color,
            disabled_foreground=palette["subtle"],
            font=self._font_small,
        )
        self.web_tab_button.pack(side="left", padx=(s(4), 0))

        legend = tk.Frame(chart_tools, background=panel_bg)
        self.chart_legend = legend
        legend.pack(side="left")
        trend_colors = (
            ("#4aa8ff", "#46c15b", "#ff8a4c", "#b58cff")
            if self.theme_mode == "dark"
            else ("#0067c0", "#107c10", "#d83b01", "#744da9")
        )
        for index, (label, color) in enumerate(
            zip(("下载", "上传", "延迟", "网页"), trend_colors)
        ):
            legend_chip = RoundedPanel(
                legend,
                parent_background=panel_bg,
                fill=palette["surface_alt"],
                border=palette["surface_alt"],
                radius=s(11),
                padding=s(2),
                fixed_height=s(28),
            )
            legend_chip.configure(width=s(58))
            legend_chip.pack(
                side="left",
                padx=(s(6) if index else 0, 0),
            )
            tk.Label(
                legend_chip.inner,
                text=f"● {label}",
                background=palette["surface_alt"],
                foreground=color,
                font=self._font_small,
            ).pack(fill="both", expand=True)

        self.chart = tk.Canvas(
            chart_card,
            background=panel_bg,
            height=s(260),
            highlightthickness=0,
        )
        self.chart.pack(
            fill="both",
            expand=True,
            padx=s(10),
            pady=(0, s(3)),
        )
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())
        self._update_chart_view_buttons()

        tk.Frame(chart_card, background=border, height=s(1)).pack(fill="x")
        self.detail_label = tk.Label(
            chart_card,
            textvariable=self.detail_var,
            background=palette["footer"],
            foreground=muted_color,
            font=self._font_small,
            justify="left",
            anchor="w",
            wraplength=s(700),
        )
        self.detail_label.pack(fill="x", padx=s(13), pady=s(8))

        self.advanced_visible = False
        self.root.after_idle(self._resize_route_note)
        self.root.after_idle(self._resize_ip_strip)

        self._last_wrap_width = 0
        self.root.bind("<Configure>", self._on_root_resize, add="+")

    def _toggle_theme(self) -> None:
        self._hide_advanced_popup()
        if self._chart_resize_after is not None:
            try:
                self.root.after_cancel(self._chart_resize_after)
            except tk.TclError:
                pass
            self._chart_resize_after = None
        self.theme_mode = "dark" if self.theme_mode == "light" else "light"
        save_ui_theme(self.theme_mode)
        history_was_open = bool(
            self.history_window is not None
            and self.history_window.winfo_exists()
        )
        if history_was_open:
            self._close_history()
        self.root.unbind("<Configure>")
        for child in self.root.winfo_children():
            child.destroy()
        self._maximize_buttons.pop(self.root, None)
        self._build_ui()
        self.root.update_idletasks()
        self._apply_windows_appearance(self.root)
        self._set_main_controls_running(self.running)
        self._draw_chart()
        if history_was_open:
            self.root.after(50, self.show_history)

    def _animate_status_dot(self) -> None:
        if not self.running:
            self._status_animation_after = None
            return
        base = "#4cc2ff" if self.theme_mode == "dark" else "#0067c0"
        glow = "#b9e7ff" if self.theme_mode == "dark" else "#65b7ef"
        wave = (math.sin(self._status_pulse_phase) + 1.0) / 2.0
        self._status_pulse_phase = (self._status_pulse_phase + 0.22) % (
            math.pi * 2
        )
        try:
            self.status_dot_label.configure(
                foreground=blend_color(base, glow, wave * 0.72)
            )
        except tk.TclError:
            self._status_animation_after = None
            return
        self._status_animation_after = self.root.after(
            55,
            self._animate_status_dot,
        )

    def _set_main_controls_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.advanced_toggle_button.configure(
            state="disabled" if running else "normal"
        )
        if running:
            self._hide_advanced_popup()
        self.profile_box.configure(state="disabled" if running else "readonly")
        self.test_kind_box.configure(state="disabled" if running else "readonly")
        self.web_profile_box.configure(state="disabled" if running else "readonly")
        self.duration_entry.configure(state="disabled" if running else "normal")
        self.download_connections_box.configure(
            state="disabled" if running else "normal"
        )
        self.upload_connections_box.configure(
            state="disabled" if running else "normal"
        )
        self.web_interval_box.configure(state="disabled" if running else "normal")
        if running or self.profile_var.get() != PROXY_PROFILE:
            self.source_box.configure(state="disabled")
        else:
            self.source_box.configure(state="readonly")
        if running:
            if self._status_animation_after is None:
                self._status_pulse_phase = 0.0
                self._animate_status_dot()
            return
        if self._status_animation_after is not None:
            try:
                self.root.after_cancel(self._status_animation_after)
            except tk.TclError:
                pass
            self._status_animation_after = None
        if self.session_var.get() == "测试完成":
            dot_color = "#6ccb5f" if self.theme_mode == "dark" else "#107c10"
        else:
            dot_color = self._theme_colors["subtle"]
        self.status_dot_label.configure(foreground=dot_color)

    def _toggle_advanced(self) -> None:
        if self.advanced_visible:
            self._hide_advanced_popup()
        else:
            self._show_advanced_popup()

    def _set_chart_view(self, mode: str) -> None:
        if mode not in ("trend", "web"):
            return
        self.chart_view_mode = mode
        self._update_chart_view_buttons()
        self._draw_chart()

    def _schedule_chart_redraw(self, _event=None) -> None:
        if self._chart_resize_after is not None:
            try:
                self.root.after_cancel(self._chart_resize_after)
            except tk.TclError:
                pass
        self._chart_resize_after = self.root.after(90, self._finish_chart_redraw)

    def _finish_chart_redraw(self) -> None:
        self._chart_resize_after = None
        self._draw_chart()

    def _update_chart_view_buttons(self) -> None:
        trend_button = getattr(self, "trend_tab_button", None)
        web_button = getattr(self, "web_tab_button", None)
        legend = getattr(self, "chart_legend", None)
        if trend_button is None or web_button is None:
            return
        palette = self._theme_colors
        active_fill = blend_color(palette["surface"], "#0067c0", 0.18)
        inactive_fill = palette["surface"]
        active_foreground = (
            "#7fc8ff" if self.theme_mode == "dark" else "#005a9e"
        )
        for button, active in (
            (trend_button, self.chart_view_mode == "trend"),
            (web_button, self.chart_view_mode == "web"),
        ):
            button.configure(
                fill=active_fill if active else inactive_fill,
                foreground=active_foreground if active else palette["muted"],
            )
        if legend is not None and legend.winfo_exists():
            if self.chart_view_mode == "trend":
                if not legend.winfo_manager():
                    legend.pack(side="left")
            else:
                legend.pack_forget()

    def _test_kind_changed(self, _event=None) -> None:
        self._set_chart_view(
            "web" if self.test_kind_var.get() == WEB_ONLY else "trend"
        )

    def _web_profile_changed(self, _event=None) -> None:
        if self.chart_view_mode == "web":
            self._draw_chart()

    def _show_advanced_popup(self) -> None:
        popup = getattr(self, "advanced_popup", None)
        if popup is None or not popup.winfo_exists() or self.running:
            return
        self.root.update_idletasks()
        popup_width = self._scaled(244)
        popup_height = self._scaled(226)
        gap = self._scaled(8)
        x = self.advanced_toggle_button.winfo_rootx()
        x += self.advanced_toggle_button.winfo_width() + gap
        y = self.advanced_toggle_button.winfo_rooty() - self._scaled(10)
        screen_right = popup.winfo_vrootx() + popup.winfo_vrootwidth()
        screen_bottom = popup.winfo_vrooty() + popup.winfo_vrootheight()
        if x + popup_width > screen_right - gap:
            x = self.advanced_toggle_button.winfo_rootx() - popup_width - gap
        y = max(gap, min(y, screen_bottom - popup_height - gap))
        popup.geometry(f"{popup_width}x{popup_height}+{max(gap, x)}+{y}")
        try:
            popup.wm_attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.advanced_visible = True
        self.advanced_toggle_button.configure(trailing_text="‹")
        popup.deiconify()
        popup.lift()
        popup.focus_force()
        self._fade_advanced_popup(0.0)

    def _fade_advanced_popup(self, alpha: float) -> None:
        popup = getattr(self, "advanced_popup", None)
        if (
            not self.advanced_visible
            or popup is None
            or not popup.winfo_exists()
        ):
            self._advanced_popup_alpha_id = None
            return
        next_alpha = min(1.0, alpha + 0.18)
        try:
            popup.wm_attributes("-alpha", next_alpha)
        except tk.TclError:
            self._advanced_popup_alpha_id = None
            return
        if next_alpha < 1.0:
            self._advanced_popup_alpha_id = self.root.after(
                16,
                self._fade_advanced_popup,
                next_alpha,
            )
        else:
            self._advanced_popup_alpha_id = None

    def _advanced_popup_focus_out(self, _event=None) -> None:
        self.root.after(25, self._hide_advanced_popup_if_unfocused)

    def _hide_advanced_popup_if_unfocused(self) -> None:
        popup = getattr(self, "advanced_popup", None)
        if popup is None or not popup.winfo_exists() or not self.advanced_visible:
            return
        focus = popup.focus_get()
        if focus is None or not str(focus).startswith(str(popup)):
            self._hide_advanced_popup()

    def _hide_advanced_popup(self) -> None:
        if self._advanced_popup_alpha_id is not None:
            try:
                self.root.after_cancel(self._advanced_popup_alpha_id)
            except tk.TclError:
                pass
            self._advanced_popup_alpha_id = None
        popup = getattr(self, "advanced_popup", None)
        if popup is not None and popup.winfo_exists():
            popup.withdraw()
        self.advanced_visible = False
        button = getattr(self, "advanced_toggle_button", None)
        if button is not None and button.winfo_exists():
            button.configure(trailing_text="›")

    def _begin_sidebar_resize(self, event: tk.Event) -> None:
        self._hide_advanced_popup()
        self._sidebar_drag_origin = (
            event.x_root,
            self.sidebar.winfo_width(),
        )
        self.sidebar_resize_handle.configure(background="#0067c0")

    def _resize_sidebar(self, event: tk.Event) -> None:
        if self._sidebar_drag_origin is None:
            return
        start_x, start_width = self._sidebar_drag_origin
        target = start_width + event.x_root - start_x
        target = max(self._scaled(188), min(self._scaled(340), target))
        self.sidebar.configure(width=target)
        self.sidebar_width = max(
            188,
            min(340, round(target / self._dpi_scale)),
        )
        if self.mode_info_label.winfo_exists():
            self.mode_info_label.configure(
                wraplength=max(
                    self._scaled(120),
                    target - self._scaled(68),
                )
            )

    def _end_sidebar_resize(self, _event=None) -> None:
        if self._sidebar_drag_origin is None:
            return
        self._sidebar_drag_origin = None
        self.sidebar_resize_handle.configure(
            background=self._theme_colors["border"]
        )
        save_sidebar_width(self.sidebar_width)
        self._resize_route_note()

    def _ip_info_changed(self, *_args) -> None:
        self._schedule_ip_strip_resize()

    def _schedule_ip_strip_resize(self) -> None:
        if self._ip_resize_after is not None:
            return
        try:
            self._ip_resize_after = self.root.after_idle(self._resize_ip_strip)
        except tk.TclError:
            self._ip_resize_after = None

    def _resize_ip_strip(self) -> None:
        self._ip_resize_after = None
        panel = getattr(self, "ip_route_panel", None)
        label = getattr(self, "ip_info_label", None)
        icon = getattr(self, "ip_route_icon_label", None)
        if any(widget is None for widget in (panel, label, icon)):
            return
        try:
            if not all(widget.winfo_exists() for widget in (panel, label, icon)):
                return
            panel_width = panel.winfo_width()
            if panel_width <= 1:
                panel_width = self._scaled(600)
            label.configure(
                wraplength=max(
                    self._scaled(320),
                    panel_width - self._scaled(90),
                )
            )
            label.update_idletasks()
            content_height = max(label.winfo_reqheight(), icon.winfo_reqheight())
            target_height = max(
                self._scaled(52),
                content_height
                + 2 * panel.panel_padding
                + self._scaled(14),
            )
            panel.fixed_height = target_height
            panel.configure(height=target_height)
        except tk.TclError:
            return

    def _resize_route_note(self) -> None:
        panel = getattr(self, "route_note_panel", None)
        label = getattr(self, "mode_info_label", None)
        title = getattr(self, "route_note_title_label", None)
        sidebar = getattr(self, "sidebar", None)
        if any(widget is None for widget in (panel, label, title, sidebar)):
            return
        try:
            if not all(
                widget.winfo_exists()
                for widget in (panel, label, title, sidebar)
            ):
                return
            sidebar_width = sidebar.winfo_width()
            if sidebar_width <= 1:
                sidebar_width = self._scaled(self.sidebar_width)
            label.configure(
                wraplength=max(
                    self._scaled(120),
                    sidebar_width - self._scaled(68),
                )
            )
            self.root.update_idletasks()
            content_height = title.winfo_reqheight() + label.winfo_reqheight()
            target_height = max(
                self._scaled(140),
                content_height + self._scaled(43),
            )
            panel.fixed_height = target_height
            panel.configure(height=target_height)
        except tk.TclError:
            return

    def _on_root_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root or event.width == self._last_wrap_width:
            return
        self._last_wrap_width = event.width
        content_width = max(self._scaled(480), event.width - self._scaled(316))
        detail_width = max(self._scaled(420), content_width - self._scaled(50))
        sidebar_width = self.sidebar.winfo_width()
        self.mode_info_label.configure(
            wraplength=max(
                self._scaled(120),
                sidebar_width - self._scaled(68),
            )
        )
        self.ip_info_label.configure(
            wraplength=max(self._scaled(360), content_width - self._scaled(90))
        )
        self._schedule_ip_strip_resize()
        self.detail_label.configure(wraplength=detail_width)

    def _build_titlebar(
        self,
        window: tk.Toplevel | tk.Tk,
        title: str,
        close_command,
    ) -> None:
        palette = getattr(self, "_theme_colors", self._theme_palette())
        bar_bg = palette["page"]
        hover_bg = palette["button_hover"]
        title_text = palette["text"]
        button_text = palette["muted"]
        bar = tk.Frame(window, background=bar_bg, height=self._scaled(44))
        bar.pack(fill="x")
        bar.pack_propagate(False)

        if self._app_icon is not None:
            try:
                target_icon_size = self._scaled(21)
                factor = max(1, self._app_icon.width() // target_icon_size)
                if self._title_icon is None:
                    self._title_icon = self._app_icon.subsample(factor, factor)
                icon = tk.Label(
                    bar,
                    image=self._title_icon,
                    background=bar_bg,
                    borderwidth=0,
                )
                icon.pack(
                    side="left",
                    padx=(self._scaled(14), self._scaled(8)),
                )
                self._bind_window_drag(icon, window)
                icon.bind("<Double-1>", lambda _event: self._toggle_maximize(window))
            except tk.TclError:
                pass

        title_label = tk.Label(
            bar,
            text=title,
            background=bar_bg,
            foreground=title_text,
            font=self._font_default,
            borderwidth=0,
        )
        title_label.pack(side="left")

        def title_button(
            text: str,
            command,
            hover: str = hover_bg,
            hover_foreground: str = title_text,
        ) -> tk.Button:
            button = tk.Button(
                bar,
                text=text,
                command=command,
                background=bar_bg,
                foreground=button_text,
                activebackground=hover,
                activeforeground=hover_foreground,
                borderwidth=0,
                highlightthickness=0,
                relief="flat",
                width=5,
                font=self._font_heading,
                cursor="hand2",
                takefocus=False,
            )
            button.bind(
                "<Enter>",
                lambda _event: button.configure(
                    background=hover,
                    foreground=hover_foreground,
                ),
            )
            button.bind(
                "<Leave>",
                lambda _event: button.configure(
                    background=bar_bg,
                    foreground=button_text,
                ),
            )
            return button

        close_button = title_button(
            "×",
            close_command,
            hover="#c42b1c",
            hover_foreground="#ffffff",
        )
        close_button.pack(side="right", fill="y")
        maximize_button = title_button(
            "□",
            lambda: self._toggle_maximize(window),
        )
        maximize_button.pack(side="right", fill="y")
        self._maximize_buttons[window] = maximize_button
        minimize_button = title_button("—", lambda: self._minimize_window(window))
        minimize_button.pack(side="right", fill="y")

        for widget in (bar, title_label):
            self._bind_window_drag(widget, window)
            widget.bind(
                "<Double-1>",
                lambda _event, w=window: self._toggle_maximize(w),
            )

    @staticmethod
    def _native_window_handle(window: tk.Toplevel | tk.Tk) -> int:
        if os.name != "nt":
            return 0
        hwnd = int(window.winfo_id())
        root_hwnd = ctypes.windll.user32.GetAncestor(hwnd, 2)
        return int(root_hwnd or hwnd)

    def _center_window(
        self,
        window: tk.Toplevel | tk.Tk,
        parent: Optional[tk.Misc] = None,
    ) -> None:
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        if parent is not None and parent.winfo_exists():
            x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
            y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
            x = max(0, min(x, window.winfo_screenwidth() - width))
            y = max(0, min(y, window.winfo_screenheight() - height))
        else:
            x = max(0, (window.winfo_screenwidth() - width) // 2)
            y = max(0, (window.winfo_screenheight() - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _apply_windows_appearance(self, window: tk.Toplevel | tk.Tk) -> None:
        if os.name != "nt":
            return
        hwnd = self._native_window_handle(window)
        self._window_handles[window] = hwnd
        user32 = ctypes.windll.user32

        get_style = user32.GetWindowLongPtrW
        set_style = user32.SetWindowLongPtrW
        get_style.restype = ctypes.c_ssize_t
        set_style.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)
        set_style.restype = ctypes.c_ssize_t
        style = get_style(hwnd, -16)
        style &= ~(0x00C00000 | 0x00040000)  # caption + native resize frame
        style |= 0x80000000 | 0x00080000 | 0x00020000 | 0x00010000
        set_style(hwnd, -16, style)
        extended_style = get_style(hwnd, -20)
        if window is self.root:
            extended_style = (extended_style | 0x00040000) & ~0x00000080
        else:
            extended_style = (extended_style | 0x00000080) & ~0x00040000
        set_style(hwnd, -20, extended_style)
        user32.SetWindowPos(
            hwnd,
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0004 | 0x0020 | 0x0200,
        )

        try:
            dwm = ctypes.windll.dwmapi
            dark_mode = ctypes.c_int(1 if self.theme_mode == "dark" else 0)
            corner = ctypes.c_int(2)  # DWMWCP_ROUND
            backdrop = ctypes.c_int(3)  # DWMSBT_TRANSIENTWINDOW (acrylic)
            host_backdrop = ctypes.c_int(1)
            no_border = ctypes.c_uint(0xFFFFFFFE)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_mode), 4)
            dwm.DwmSetWindowAttribute(hwnd, 17, ctypes.byref(host_backdrop), 4)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
            dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(backdrop), 4)
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(no_border), 4)
        except (AttributeError, OSError):
            pass
        self._install_resize_bindings(window)

    def _bind_window_drag(
        self,
        widget: tk.Widget,
        window: tk.Toplevel | tk.Tk,
    ) -> None:
        widget.bind(
            "<ButtonPress-1>",
            lambda event, w=window: self._begin_window_drag(w, event),
        )
        widget.bind(
            "<B1-Motion>",
            lambda event, w=window: self._move_window(w, event),
        )
        widget.bind(
            "<ButtonRelease-1>",
            lambda _event, w=window: self._end_window_drag(w),
        )

    def _begin_window_drag(
        self,
        window: tk.Toplevel | tk.Tk,
        event: tk.Event,
    ) -> None:
        if window in self._custom_maximized:
            self._toggle_maximize(window)
            window.update_idletasks()
            new_x = event.x_root - window.winfo_width() // 2
            new_y = max(0, event.y_root - self._scaled(22))
            window.geometry(f"+{new_x}+{new_y}")
        self._drag_states[window] = (
            event.x_root,
            event.y_root,
            window.winfo_x(),
            window.winfo_y(),
        )

    def _move_window(self, window: tk.Toplevel | tk.Tk, event: tk.Event) -> None:
        state = self._drag_states.get(window)
        if state is None:
            return
        start_x, start_y, window_x, window_y = state
        new_x = window_x + event.x_root - start_x
        new_y = max(0, window_y + event.y_root - start_y)
        window.geometry(f"+{new_x}+{new_y}")

    def _end_window_drag(self, window: tk.Toplevel | tk.Tk) -> None:
        self._drag_states.pop(window, None)

    def _minimize_window(self, window: tk.Toplevel | tk.Tk) -> None:
        if os.name == "nt":
            hwnd = self._window_handles.get(window) or self._native_window_handle(window)
            ctypes.windll.user32.ShowWindow(hwnd, 6)
        else:
            window.iconify()

    def _toggle_maximize(self, window: tk.Toplevel | tk.Tk) -> None:
        maximized = window in self._custom_maximized
        if maximized:
            geometry = self._restore_geometries.pop(window, "")
            self._custom_maximized.discard(window)
            if geometry:
                window.geometry(geometry)
        elif os.name == "nt":
            hwnd = self._window_handles.get(window) or self._native_window_handle(window)
            self._restore_geometries[window] = window.geometry()

            class Rect(ctypes.Structure):
                _fields_ = (
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                )

            class MonitorInfo(ctypes.Structure):
                _fields_ = (
                    ("size", ctypes.c_ulong),
                    ("monitor", Rect),
                    ("work", Rect),
                    ("flags", ctypes.c_ulong),
                )

            monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, 2)
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info))
            work = info.work
            window.geometry(
                f"{work.right - work.left}x{work.bottom - work.top}"
                f"+{work.left}+{work.top}"
            )
            self._custom_maximized.add(window)
        else:
            self._restore_geometries[window] = window.geometry()
            window.state("zoomed")
            self._custom_maximized.add(window)
        button = self._maximize_buttons.get(window)
        if button is not None:
            button.configure(text="□" if maximized else "❐")

    def _install_resize_bindings(self, window: tk.Toplevel | tk.Tk) -> None:
        if window in self._resize_bound:
            return
        self._resize_bound.add(window)
        window.bind(
            "<Motion>",
            lambda event, w=window: self._update_resize_cursor(w, event),
            add="+",
        )
        window.bind(
            "<ButtonPress-1>",
            lambda event, w=window: self._begin_resize(w, event),
            add="+",
        )
        window.bind(
            "<B1-Motion>",
            lambda event, w=window: self._resize_window(w, event),
            add="+",
        )
        window.bind(
            "<ButtonRelease-1>",
            lambda _event, w=window: self._end_resize(w),
            add="+",
        )

    def _resize_edge(self, window: tk.Toplevel | tk.Tk, event: tk.Event) -> str:
        margin = self._scaled(7)
        x = event.x_root - window.winfo_rootx()
        y = event.y_root - window.winfo_rooty()
        left = x <= margin
        right = x >= window.winfo_width() - margin
        top = y <= margin
        bottom = y >= window.winfo_height() - margin
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if bottom and left:
            return "sw"
        if bottom and right:
            return "se"
        if left:
            return "w"
        if right:
            return "e"
        if top:
            return "n"
        if bottom:
            return "s"
        return ""

    def _update_resize_cursor(
        self,
        window: tk.Toplevel | tk.Tk,
        event: tk.Event,
    ) -> None:
        if window in self._resize_states:
            return
        edge = self._resize_edge(window, event)
        cursors = {
            "n": "size_ns",
            "s": "size_ns",
            "e": "size_we",
            "w": "size_we",
            "nw": "size_nw_se",
            "se": "size_nw_se",
            "ne": "size_ne_sw",
            "sw": "size_ne_sw",
        }
        window.configure(cursor=cursors.get(edge, ""))

    def _begin_resize(
        self,
        window: tk.Toplevel | tk.Tk,
        event: tk.Event,
    ) -> Optional[str]:
        if window in self._custom_maximized:
            return None
        if os.name == "nt":
            hwnd = self._window_handles.get(window) or self._native_window_handle(window)
            if ctypes.windll.user32.IsZoomed(hwnd):
                return None
        edge = self._resize_edge(window, event)
        if not edge:
            return None
        self._resize_states[window] = (
            edge,
            event.x_root,
            event.y_root,
            window.winfo_x(),
            window.winfo_y(),
            window.winfo_width(),
            window.winfo_height(),
        )
        return "break"

    def _resize_window(self, window: tk.Toplevel | tk.Tk, event: tk.Event) -> None:
        state = self._resize_states.get(window)
        if state is None:
            return
        edge, start_x, start_y, x, y, width, height = state
        dx = event.x_root - start_x
        dy = event.y_root - start_y
        min_width, min_height = window.minsize()
        new_x, new_y = x, y
        new_width, new_height = width, height
        if "e" in edge:
            new_width = max(min_width, width + dx)
        if "s" in edge:
            new_height = max(min_height, height + dy)
        if "w" in edge:
            new_width = max(min_width, width - dx)
            new_x = x + width - new_width
        if "n" in edge:
            new_height = max(min_height, height - dy)
            new_y = y + height - new_height
        window.geometry(f"{new_width}x{new_height}+{new_x}+{new_y}")

    def _end_resize(self, window: tk.Toplevel | tk.Tk) -> None:
        self._resize_states.pop(window, None)

    def _active_profile(self) -> dict:
        profile = dict(PROFILES[self.profile_var.get()])
        if self.profile_var.get() == PROXY_PROFILE:
            profile.update(PROXY_SOURCES[self.proxy_source_var.get()])
        return profile

    def _profile_changed(self, _event=None) -> None:
        if self.profile_var.get() == PROXY_PROFILE:
            self.source_box.configure(state="readonly")
        else:
            self.source_box.configure(state="disabled")
        self.mode_info_var.set(self._active_profile()["info"])
        self.root.after_idle(self._resize_route_note)
        self._refresh_ip()

    def _source_changed(self, _event=None) -> None:
        self.mode_info_var.set(self._active_profile()["info"])
        self.root.after_idle(self._resize_route_note)

    def _refresh_ip(self) -> None:
        self.lookup_serial += 1
        serial = self.lookup_serial
        profile = self._active_profile()
        self.public_ip = ""
        self.ip_region = ""
        self.ip_isp = ""

        if not profile["download_direct"]:
            proxies = urllib.request.getproxies()
            if not (proxies.get("https") or proxies.get("http")):
                self.ip_info_var.set("出口 IP：未检测到 Clash 系统代理")
                return

        route_name = "直连线路" if profile["download_direct"] else "Clash 代理"
        self.ip_info_var.set(f"出口 IP：正在通过{route_name}查询…")
        thread = threading.Thread(
            target=self._ip_lookup_worker,
            args=(serial, profile["download_direct"], route_name),
            daemon=True,
        )
        thread.start()

    def _ip_lookup_worker(
        self,
        serial: int,
        direct: bool,
        route_name: str,
    ) -> None:
        try:
            opener = (
                urllib.request.build_opener(urllib.request.ProxyHandler({}))
                if direct
                else urllib.request.build_opener()
            )
            request = urllib.request.Request(
                IP_LOOKUP_URL,
                headers={"User-Agent": "net-stability-test/1.5"},
            )
            with opener.open(request, timeout=10) as response:
                data = json.load(response)
            if data.get("status") != "success":
                raise RuntimeError(data.get("message") or "IP 查询失败")

            public_ip = str(data.get("query") or "未知")
            location_parts = []
            for value in (
                data.get("country"),
                data.get("regionName"),
                data.get("city"),
            ):
                if value and value not in location_parts:
                    location_parts.append(str(value))
            region = " · ".join(location_parts) or "未知"
            isp = str(data.get("isp") or "未知")
            result = f"出口 IP：{public_ip}　地区：{region}　运营商：{isp}　({route_name})"
            values = (public_ip, region, isp, result)
        except Exception as exc:
            values = ("", "", "", f"出口 IP：查询失败（{exc}）")

        try:
            self.root.after(0, self._set_ip_info, serial, *values)
        except tk.TclError:
            pass

    def _set_ip_info(
        self,
        serial: int,
        public_ip: str,
        region: str,
        isp: str,
        text: str,
    ) -> None:
        if serial != self.lookup_serial:
            return
        self.public_ip = public_ip
        self.ip_region = region
        self.ip_isp = isp
        self.ip_info_var.set(text)

    def show_history(self) -> None:
        if self.history_window is not None and self.history_window.winfo_exists():
            self.history_window.deiconify()
            self.history_window.lift()
            self._refresh_history()
            return

        window = tk.Toplevel(self.root)
        self.history_window = window
        window.title("测速历史")
        window.withdraw()
        window.geometry(f"{self._scaled(1280)}x{self._scaled(660)}")
        window.minsize(self._scaled(960), self._scaled(480))
        window.configure(background="#eaf0f6")
        try:
            if self._app_icon is not None:
                window.iconphoto(True, self._app_icon)
            if os.name == "nt" and os.path.exists(APP_ICON_ICO):
                window.iconbitmap(default=APP_ICON_ICO)
        except tk.TclError:
            pass
        window.protocol("WM_DELETE_WINDOW", self._close_history)
        self._build_titlebar(window, "测速历史", self._close_history)
        window.overrideredirect(True)

        outer = ttk.Frame(
            window,
            padding=(self._scaled(18), self._scaled(16)),
            style="App.TFrame",
        )
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="测速历史", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="按时间倒序显示所有测试；双击任意一行可直接打开原始 CSV 报告。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(self._scaled(1), self._scaled(2)))
        ttk.Label(
            outer,
            text=f"日志目录：{LOG_DIR}",
            style="Subtitle.TLabel",
        ).pack(
            anchor="w",
            pady=(0, self._scaled(10)),
        )

        table_frame = ttk.Frame(
            outer,
            padding=self._scaled(1),
            style="Panel.TFrame",
        )
        table_frame.pack(fill="both", expand=True)
        columns = (
            "started",
            "status",
            "profile",
            "kind",
            "source",
            "download",
            "upload",
            "latency",
            "web",
            "errors",
            "region",
        )
        tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
            style="History.Treeview",
        )
        self.history_tree = tree
        headings = {
            "started": "开始时间",
            "status": "状态",
            "profile": "模式",
            "kind": "测试类型",
            "source": "测速源",
            "download": "平均下载",
            "upload": "平均上传",
            "latency": "延迟 P95",
            "web": "最慢 P95 / 响应率",
            "errors": "错误 D/U",
            "region": "出口地区",
        }
        widths = {
            "started": 150,
            "status": 65,
            "profile": 120,
            "kind": 125,
            "source": 125,
            "download": 95,
            "upload": 95,
            "latency": 90,
            "web": 135,
            "errors": 80,
            "region": 205,
        }
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(
                column,
                width=self._scaled(widths[column]),
                minwidth=self._scaled(60),
                anchor="center" if column not in ("started", "region") else "w",
            )
        tree.tag_configure("even", background=self._theme_colors["surface_alt"])
        tree.tag_configure("odd", background=self._theme_colors["surface"])
        tree.tag_configure(
            "running",
            foreground="#4cc2ff" if self.theme_mode == "dark" else "#1769aa",
        )

        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        tree.bind("<Double-1>", lambda _event: self._open_selected_history())

        footer = ttk.Frame(outer, style="App.TFrame")
        footer.pack(fill="x", pady=(self._scaled(10), 0))
        self.history_count_var = tk.StringVar(value="")
        ttk.Label(
            footer,
            textvariable=self.history_count_var,
            style="Subtitle.TLabel",
        ).pack(side="left")
        ttk.Button(footer, text="刷新", command=self._refresh_history).pack(
            side="right"
        )
        ttk.Button(
            footer,
            text="打开报告",
            command=self._open_selected_history,
        ).pack(side="right", padx=(0, self._scaled(8)))
        ttk.Button(
            footer,
            text="打开日志目录",
            command=self._open_history_folder,
        ).pack(side="right", padx=(0, self._scaled(8)))

        self._refresh_history()
        window.update_idletasks()
        self._center_window(window, self.root)
        self._apply_windows_appearance(window)
        window.deiconify()
        window.after(20, self._apply_windows_appearance, window)
        window.after(5000, self._history_auto_refresh)

    def _close_history(self) -> None:
        if self.history_window is not None:
            self._window_handles.pop(self.history_window, None)
            self._maximize_buttons.pop(self.history_window, None)
            self._resize_states.pop(self.history_window, None)
            self._resize_bound.discard(self.history_window)
            self._drag_states.pop(self.history_window, None)
            self._restore_geometries.pop(self.history_window, None)
            self._custom_maximized.discard(self.history_window)
            self.history_window.destroy()
        self.history_window = None
        self.history_tree = None
        self.history_paths = {}

    def _history_auto_refresh(self) -> None:
        if self.history_window is None or not self.history_window.winfo_exists():
            return
        self._refresh_history()
        self.history_window.after(5000, self._history_auto_refresh)

    def _refresh_history(self) -> None:
        tree = self.history_tree
        if tree is None or not tree.winfo_exists():
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        self.history_paths = {}

        try:
            paths = sorted(
                (
                    os.path.join(LOG_DIR, name)
                    for name in os.listdir(LOG_DIR)
                    if name.lower().endswith(".csv")
                ),
                key=os.path.getmtime,
                reverse=True,
            )
        except OSError as exc:
            messagebox.showerror("历史读取失败", str(exc), parent=self.history_window)
            return

        count = 0
        for path in paths:
            summary = summarize_history_log(path)
            if summary is None:
                continue
            is_running = self.running and os.path.abspath(path) == os.path.abspath(
                self.csv_path
            )
            status = "进行中" if is_running else "完成"
            download_text = (
                "—"
                if summary["download_avg"] is None
                else f"{summary['download_avg']:.2f} Mbps"
            )
            upload_text = (
                "—"
                if summary["upload_avg"] is None
                else f"{summary['upload_avg']:.2f} Mbps"
            )
            latency_text = (
                "—"
                if summary["latency_p95"] is None
                else f"{summary['latency_p95']:.0f} ms"
            )
            if summary["web_p95"] is None:
                web_text = "—"
            elif summary["web_success"] is None:
                web_text = f"{summary['web_p95']:.0f} ms"
            else:
                web_text = (
                    f"{summary['web_p95']:.0f} ms / "
                    f"{summary['web_success']:.0f}%"
                )
            item_id = tree.insert(
                "",
                "end",
                values=(
                    summary["started"],
                    status,
                    summary["profile"],
                    summary["test_kind"],
                    summary["source"],
                    download_text,
                    upload_text,
                    latency_text,
                    web_text,
                    f"{summary['download_errors']}/{summary['upload_errors']}",
                    summary["region"],
                ),
                tags=(
                    ("running", "even" if count % 2 == 0 else "odd")
                    if is_running
                    else ("even" if count % 2 == 0 else "odd",)
                ),
            )
            self.history_paths[item_id] = summary["path"]
            count += 1

        if hasattr(self, "history_count_var"):
            self.history_count_var.set(f"共 {count} 份报告 · 双击可打开")

    def _open_selected_history(self) -> None:
        tree = self.history_tree
        if tree is None:
            return
        selection = tree.selection()
        if not selection:
            messagebox.showinfo(
                "打开报告",
                "请先选择一份测速报告。",
                parent=self.history_window,
            )
            return
        path = self.history_paths.get(selection[0])
        if path and os.path.exists(path):
            os.startfile(path)

    def _open_history_folder(self) -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        os.startfile(LOG_DIR)

    def start(self) -> None:
        if self.running:
            return
        try:
            duration = int(self.duration_var.get())
            connections = int(self.connections_var.get())
            upload_connections = int(self.upload_connections_var.get())
            web_interval = float(self.web_interval_var.get())
            if (
                duration <= 0
                or connections < 1
                or upload_connections < 1
                or web_interval < 1
            ):
                raise ValueError
        except ValueError:
            messagebox.showerror(
                "参数错误",
                "测试时长必须大于 0，并发连接和网页采样间隔必须至少为 1。",
            )
            return

        profile = self._active_profile()
        test_kind = self.test_kind_var.get()
        run_download = test_kind in (DOWNLOAD_ONLY, DUPLEX_TEST)
        run_upload = test_kind in (UPLOAD_ONLY, DUPLEX_TEST)
        if test_kind == WEB_ONLY and self.web_profile_var.get() == WEB_PROFILE_OFF:
            messagebox.showerror(
                "未选择网页目标",
                "“仅网页体验”需要选择综合、ChatGPT 专项或常用海外网站。",
            )
            return
        if profile["upload_use_proxy"]:
            proxies = urllib.request.getproxies()
            if not (proxies.get("https") or proxies.get("http")):
                messagebox.showerror(
                    "未检测到系统代理",
                    "Clash 代理模式需要先开启 Clash 的“系统代理”。",
                )
                return
            # Public international test endpoints are stable with four
            # concurrent transfers while still filling most proxy nodes.
            connections = min(connections, 4)
            upload_connections = min(upload_connections, 4)
            self.connections_var.set(str(connections))
            self.upload_connections_var.set(str(upload_connections))

        self._refresh_ip()

        self.state = SharedState()
        self.upload_state = UploadState()
        self.web_state = WebExperienceState()
        self.stop_event = threading.Event()
        self.workers = []
        self.samples.clear()
        self.peak_mbps = 0.0
        self.upload_peak_mbps = 0.0
        self.started = self.last_time = time.perf_counter()
        self.last_bytes = 0
        self.last_upload_bytes = 0
        self.upload_speed_window.clear()
        self.upload_speed_window.append((self.started, 0))
        self.last_latency_count = 0
        self.last_latency_failures = 0
        self.last_web_count = 0
        self.web_ttfb_var.set("-- ms")
        self.web_success_var.set("--")

        timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        os.makedirs(LOG_DIR, exist_ok=True)
        log_filename = (
            f"network_{profile['log_slug']}_{TEST_KIND_SLUGS[test_kind]}_gui_"
            f"{timestamp}.csv"
        )
        self.csv_path = os.path.join(LOG_DIR, log_filename)
        self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8-sig")
        fieldnames = [
            "timestamp",
            "profile",
            "test_kind",
            "proxy_source",
            "public_ip",
            "ip_region",
            "ip_isp",
            "elapsed_sec",
            "interval_sec",
            "interval_mbps",
            "avg_mbps",
            "total_gb",
            "upload_interval_mbps",
            "upload_current_15s_mbps",
            "upload_avg_mbps",
            "uploaded_gb",
            "requests",
            "download_errors",
            "active_downloads",
            "upload_requests",
            "upload_errors",
            "active_uploads",
            "latency_avg_ms",
            "latency_p95_ms",
            "latency_failures",
            "web_profile",
            "web_target",
            "web_status",
            "web_error",
            "web_ttfb_ms",
            "web_total_ms",
            "web_ttfb_p95_ms",
            "web_jitter_ms",
            "web_success_rate",
            "web_failures",
            "web_target_stats",
            "web_grade",
            "web_conclusion",
        ]
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=fieldnames)
        self.csv_writer.writeheader()

        if run_download:
            for worker_id in range(1, connections + 1):
                thread = threading.Thread(
                    target=download_worker,
                    args=(
                        worker_id,
                        self.state,
                        self.stop_event,
                        profile["download_url"],
                        profile["download_payload"],
                        profile["download_timeout"],
                        256 * 1024,
                        profile["download_direct"],
                    ),
                    daemon=True,
                )
                self.workers.append(thread)
                thread.start()

        if run_upload:
            for worker_id in range(1, upload_connections + 1):
                thread = threading.Thread(
                    target=upload_worker,
                    args=(
                        worker_id,
                        self.upload_state,
                        self.stop_event,
                        profile["upload_payload"],
                        profile["upload_timeout"],
                        256 * 1024,
                        profile["upload_host"],
                        profile["upload_port"],
                        profile["upload_path"],
                        profile["upload_use_proxy"],
                    ),
                    daemon=True,
                )
                self.workers.append(thread)
                thread.start()

        web_targets = WEB_TEST_PROFILES[self.web_profile_var.get()]
        for target_index, (target, url) in enumerate(web_targets):
            thread = threading.Thread(
                target=web_probe_worker,
                args=(
                    target,
                    url,
                    self.web_state,
                    self.stop_event,
                    profile["download_direct"],
                    target_index * 0.25,
                    web_interval,
                ),
                daemon=True,
            )
            self.workers.append(thread)
            thread.start()

        if profile["download_direct"]:
            latency_thread = threading.Thread(
                target=latency_worker,
                args=(
                    self.state,
                    self.stop_event,
                    infer_latency_host(profile["download_url"]),
                    443,
                    5.0,
                    1.0,
                ),
                daemon=True,
            )
        else:
            latency_thread = threading.Thread(
                target=proxy_latency_worker,
                args=(
                    self.state,
                    self.stop_event,
                    profile["latency_url"],
                    8.0,
                    1.0,
                ),
                daemon=True,
            )
        self.workers.append(latency_thread)
        latency_thread.start()

        self.running = True
        self.session_var.set("测试中")
        self.elapsed_var.set("0 秒")
        if test_kind == WEB_ONLY:
            self.chart_view_mode = "web"
            self._update_chart_view_buttons()
        self._set_main_controls_running(True)
        self.detail_var.set(
            f"测试中 · {test_kind} · 结果保存到 {self.csv_path}"
        )
        self._refresh_history()
        self._draw_chart()
        self.after_id = self.root.after(self.SAMPLE_MS, self._tick)

    def _tick(self) -> None:
        self.after_id = None
        if (
            not self.running
            or self.state is None
            or self.upload_state is None
            or self.web_state is None
        ):
            return

        now = time.perf_counter()
        elapsed = now - self.started
        self.elapsed_var.set(f"{elapsed:.0f} 秒")
        snapshot = self.state.snapshot()
        upload_snapshot = self.upload_state.snapshot()
        web_snapshot = self.web_state.snapshot()
        interval_sec = max(0.001, now - self.last_time)
        interval_bytes = snapshot["downloaded_bytes"] - self.last_bytes
        upload_interval_bytes = (
            upload_snapshot["uploaded_bytes"] - self.last_upload_bytes
        )
        current_mbps = mbps(interval_bytes, interval_sec)
        average_mbps = mbps(snapshot["downloaded_bytes"], elapsed)
        upload_raw_interval_mbps = mbps(upload_interval_bytes, interval_sec)
        self.upload_speed_window.append(
            (now, upload_snapshot["uploaded_bytes"])
        )
        cutoff = now - self.UPLOAD_SMOOTH_SECONDS
        while (
            len(self.upload_speed_window) > 2
            and self.upload_speed_window[1][0] <= cutoff
        ):
            self.upload_speed_window.popleft()
        upload_base_time, upload_base_bytes = self.upload_speed_window[0]
        upload_current_mbps = mbps(
            upload_snapshot["uploaded_bytes"] - upload_base_bytes,
            max(0.001, now - upload_base_time),
        )
        upload_average_mbps = mbps(upload_snapshot["uploaded_bytes"], elapsed)
        self.peak_mbps = max(self.peak_mbps, current_mbps)
        self.upload_peak_mbps = max(self.upload_peak_mbps, upload_current_mbps)

        new_latencies = snapshot["latency_samples_ms"][self.last_latency_count :]
        if new_latencies:
            latency_avg = sum(new_latencies) / len(new_latencies)
            latency_p95 = percentile(new_latencies, 0.95)
        elif snapshot["latency_samples_ms"]:
            latency_avg = snapshot["latency_samples_ms"][-1]
            latency_p95 = latency_avg
        else:
            latency_avg = None
            latency_p95 = None
        interval_latency_failures = (
            snapshot["latency_failures"] - self.last_latency_failures
        )

        web_samples = web_snapshot["samples"]
        new_web_count = max(0, web_snapshot["request_count"] - self.last_web_count)
        new_web_samples = web_samples[-new_web_count:] if new_web_count else []
        new_web_successes = [item for item in new_web_samples if item["success"]]
        web_current = (
            max(float(item["ttfb_ms"]) for item in new_web_successes)
            if new_web_successes
            else None
        )
        web_total_current = (
            max(float(item["total_ms"]) for item in new_web_successes)
            if new_web_successes
            else None
        )
        web_recent = web_samples[-120:]
        web_recent_successes = [item for item in web_recent if item["success"]]
        web_success_rate = (
            len(web_recent_successes) / len(web_recent) * 100
            if web_recent
            else None
        )
        last_by_target: dict[str, float] = {}
        web_deltas: list[float] = []
        target_totals: dict[str, int] = {}
        target_successes: dict[str, list[float]] = {}
        for item in web_recent:
            target = str(item["target"])
            target_totals[target] = target_totals.get(target, 0) + 1
            if not item["success"]:
                continue
            value = float(item["ttfb_ms"])
            target_successes.setdefault(target, []).append(value)
            if target in last_by_target:
                web_deltas.append(abs(value - last_by_target[target]))
            last_by_target[target] = value
        web_jitter = sum(web_deltas) / len(web_deltas) if web_deltas else None
        target_stats_parts: list[str] = []
        target_p95_values: list[float] = []
        for target in target_totals:
            values = target_successes.get(target, [])
            success = len(values) / target_totals[target] * 100
            target_p95 = percentile(values, 0.95)
            if target_p95 is not None:
                target_p95_values.append(target_p95)
            response = "失败" if target_p95 is None else f"{target_p95:.0f}ms"
            target_stats_parts.append(f"{target}:{response}/{success:.0f}%")
        web_p95 = max(target_p95_values) if target_p95_values else None
        target_stats = "; ".join(target_stats_parts)
        latest_web = web_samples[-1] if web_samples else None
        _analysis_rows, web_analysis = self._web_analysis_data()

        self.samples.append(
            (elapsed, current_mbps, upload_current_mbps, latency_avg, web_current)
        )
        self.current_var.set(
            f"{current_mbps:.2f} Mbps"
        )
        self.average_var.set(
            f"{average_mbps:.2f} Mbps"
        )
        self.peak_var.set(
            f"{self.peak_mbps:.2f} Mbps"
        )
        self.download_summary_var.set(
            f"{current_mbps / 8:.2f} MB/s"
        )
        self.upload_current_var.set(
            f"{upload_current_mbps:.2f} Mbps"
        )
        self.upload_average_var.set(
            f"{upload_average_mbps:.2f} Mbps"
        )
        self.upload_peak_var.set(
            f"{self.upload_peak_mbps:.2f} Mbps"
        )
        self.upload_summary_var.set(
            f"{upload_current_mbps / 8:.2f} MB/s"
        )
        self.latency_var.set("-- ms" if latency_avg is None else f"{latency_avg:.1f} ms")
        if web_p95 is None:
            self.web_ttfb_var.set("-- ms")
        elif web_jitter is None:
            self.web_ttfb_var.set(f"{web_p95:.0f} ms / --")
        else:
            self.web_ttfb_var.set(f"{web_p95:.0f} / {web_jitter:.0f} ms")
        self.web_success_var.set(
            "--" if web_success_rate is None else f"{web_success_rate:.1f}%"
        )
        if web_success_rate is not None and web_success_rate < 95:
            web_health = "不稳定"
        elif web_p95 is None:
            web_health = "等待采样"
        elif web_p95 <= 500:
            web_health = "流畅"
        elif web_p95 <= 1000:
            web_health = "偶有迟滞"
        elif web_p95 <= 2000:
            web_health = "明显迟滞"
        else:
            web_health = "严重卡顿"
        total_gb = snapshot["downloaded_bytes"] / 1_000_000_000
        uploaded_gb = upload_snapshot["uploaded_bytes"] / 1_000_000_000
        self.detail_var.set(
            f"{self.test_kind_var.get()} · 已运行 {elapsed:.0f} 秒 · "
            f"下载 {total_gb:.3f} GB · 上传 {uploaded_gb:.3f} GB · "
            f"下载错误 {snapshot['errors']} · 上传错误 {upload_snapshot['errors']} · "
            f"延迟失败 {snapshot['latency_failures']} · "
            f"网页 {web_health} · 失败 {web_snapshot['failures']} · "
            f"日志 {self.csv_path}"
        )

        if self.csv_writer is not None and self.csv_file is not None:
            self.csv_writer.writerow(
                {
                    "timestamp": now_iso(),
                    "profile": self.profile_var.get(),
                    "test_kind": self.test_kind_var.get(),
                    "proxy_source": (
                        self.proxy_source_var.get()
                        if self.profile_var.get() == PROXY_PROFILE
                        else ""
                    ),
                    "public_ip": self.public_ip,
                    "ip_region": self.ip_region,
                    "ip_isp": self.ip_isp,
                    "elapsed_sec": round(elapsed, 2),
                    "interval_sec": round(interval_sec, 2),
                    "interval_mbps": round(current_mbps, 2),
                    "avg_mbps": round(average_mbps, 2),
                    "total_gb": round(total_gb, 4),
                    "upload_interval_mbps": round(upload_raw_interval_mbps, 2),
                    "upload_current_15s_mbps": round(upload_current_mbps, 2),
                    "upload_avg_mbps": round(upload_average_mbps, 2),
                    "uploaded_gb": round(uploaded_gb, 4),
                    "requests": snapshot["request_count"],
                    "download_errors": snapshot["errors"],
                    "active_downloads": snapshot["active_downloads"],
                    "upload_requests": upload_snapshot["request_count"],
                    "upload_errors": upload_snapshot["errors"],
                    "active_uploads": upload_snapshot["active_uploads"],
                    "latency_avg_ms": round(latency_avg, 2) if latency_avg is not None else "",
                    "latency_p95_ms": round(latency_p95, 2) if latency_p95 is not None else "",
                    "latency_failures": interval_latency_failures,
                    "web_profile": self.web_profile_var.get(),
                    "web_target": latest_web["target"] if latest_web else "",
                    "web_status": latest_web["status"] if latest_web else "",
                    "web_error": latest_web["error"] if latest_web else "",
                    "web_ttfb_ms": round(web_current, 2) if web_current is not None else "",
                    "web_total_ms": (
                        round(web_total_current, 2)
                        if web_total_current is not None
                        else ""
                    ),
                    "web_ttfb_p95_ms": round(web_p95, 2) if web_p95 is not None else "",
                    "web_jitter_ms": round(web_jitter, 2) if web_jitter is not None else "",
                    "web_success_rate": (
                        round(web_success_rate, 2)
                        if web_success_rate is not None
                        else ""
                    ),
                    "web_failures": web_snapshot["failures"],
                    "web_target_stats": target_stats,
                    "web_grade": web_analysis["grade"],
                    "web_conclusion": web_analysis["conclusion"],
                }
            )
            self.csv_file.flush()

        self.last_time = now
        self.last_bytes = snapshot["downloaded_bytes"]
        self.last_upload_bytes = upload_snapshot["uploaded_bytes"]
        self.last_latency_count = len(snapshot["latency_samples_ms"])
        self.last_latency_failures = snapshot["latency_failures"]
        self.last_web_count = web_snapshot["request_count"]
        self._draw_chart()

        duration = int(self.duration_var.get())
        if elapsed >= duration:
            self.stop(completed=True)
        else:
            self.after_id = self.root.after(self.SAMPLE_MS, self._tick)

    def _web_analysis_data(self) -> tuple[list[dict], dict]:
        profile_name = self.web_profile_var.get()
        targets = [name for name, _url in WEB_TEST_PROFILES[profile_name]]
        snapshot = (
            self.web_state.snapshot()
            if self.web_state is not None
            else {"samples": [], "request_count": 0, "failures": 0}
        )
        recent = snapshot["samples"][-240:]
        rows: list[dict] = []
        all_values: list[float] = []
        all_deltas: list[float] = []
        for target in targets:
            samples = [item for item in recent if item["target"] == target]
            successes = [item for item in samples if item["success"]]
            values = [float(item["ttfb_ms"]) for item in successes]
            all_values.extend(values)
            all_deltas.extend(
                abs(values[index] - values[index - 1])
                for index in range(1, len(values))
            )
            success_rate = len(successes) / len(samples) * 100 if samples else None
            p95 = percentile(values, 0.95)
            latest = samples[-1] if samples else None
            latest_ms = (
                float(latest["ttfb_ms"])
                if latest is not None and latest["ttfb_ms"] is not None
                else None
            )
            if not samples:
                assessment = "等待"
            elif success_rate is not None and success_rate < 80:
                assessment = "不可用"
            elif success_rate is not None and success_rate < 95:
                assessment = "不稳定"
            elif p95 is None:
                assessment = "失败"
            elif p95 <= 500:
                assessment = "流畅"
            elif p95 <= 1000:
                assessment = "可用"
            elif p95 <= 2000:
                assessment = "偏慢"
            else:
                assessment = "卡顿"
            rows.append(
                {
                    "target": target,
                    "samples": len(samples),
                    "latest": latest,
                    "latest_ms": latest_ms,
                    "p95": p95,
                    "success_rate": success_rate,
                    "assessment": assessment,
                }
            )

        total = sum(row["samples"] for row in rows)
        successes = sum(
            round(row["samples"] * row["success_rate"] / 100)
            for row in rows
            if row["success_rate"] is not None
        )
        availability = successes / total * 100 if total else None
        overall_p95 = percentile(all_values, 0.95)
        jitter = sum(all_deltas) / len(all_deltas) if all_deltas else None
        measured_rows = [row for row in rows if row["p95"] is not None]
        slowest = (
            max(measured_rows, key=lambda row: float(row["p95"]))
            if measured_rows
            else None
        )
        if not targets:
            grade = "已关闭"
            tone = "muted"
            conclusion = "当前配置未启用网页体验测试。"
        elif not total:
            grade = "等待采样"
            tone = "muted"
            conclusion = "开始测试后会分别统计每个站点的响应、成功率和 P95。"
        elif availability is not None and availability < 80:
            grade = "较差"
            tone = "bad"
            conclusion = (
                f"请求可用性仅 {availability:.0f}%，主要问题是访问失败；"
                "建议优先检查代理节点、DNS 或站点风控。"
            )
        elif overall_p95 is not None and overall_p95 > 2000:
            grade = "严重卡顿"
            tone = "bad"
            conclusion = (
                f"综合 P95 为 {overall_p95:.0f} ms，首字节响应过慢；"
                "即使测速带宽较高，打开网页仍会明显迟滞。"
            )
        elif (
            (availability is not None and availability < 95)
            or (overall_p95 is not None and overall_p95 > 1000)
        ):
            grade = "不稳定"
            tone = "warn"
            bottleneck = slowest["target"] if slowest is not None else "部分站点"
            conclusion = (
                f"主要瓶颈在 {bottleneck}；响应或成功率波动较大，"
                "网页端可能出现间歇性卡顿。"
            )
        elif overall_p95 is not None and overall_p95 > 500:
            grade = "一般"
            tone = "warn"
            bottleneck = slowest["target"] if slowest is not None else "最慢站点"
            conclusion = (
                f"整体可以使用，但 {bottleneck} 的首字节响应偏慢，"
                "复杂网页首次打开可能有短暂停顿。"
            )
        else:
            grade = "良好"
            tone = "good"
            conclusion = "各目标站点的成功率和首字节响应正常，网页体验应较流畅。"
        return rows, {
            "profile": profile_name,
            "total": total,
            "availability": availability,
            "p95": overall_p95,
            "jitter": jitter,
            "slowest": slowest,
            "grade": grade,
            "tone": tone,
            "conclusion": conclusion,
        }

    def _draw_web_analysis(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        scale = self._scaled
        palette = self._theme_colors
        width = max(canvas.winfo_width(), scale(420))
        height = max(canvas.winfo_height(), scale(250))
        chart_background = palette["surface_alt"]
        canvas.create_polygon(
            rounded_polygon_points(width, height, scale(18)),
            smooth=True,
            splinesteps=28,
            fill=chart_background,
            outline=palette["border"],
            width=1,
        )
        rows, overall = self._web_analysis_data()
        self.chart_title_var.set("网页体验分析")
        self.chart_subtitle_var.set(
            f"{overall['profile']} · {len(rows)} 个目标 · "
            f"累计 {overall['total']} 次请求"
        )
        tone_colors = {
            "good": "#46c15b" if self.theme_mode == "dark" else "#107c10",
            "warn": "#ffb454" if self.theme_mode == "dark" else "#b85c00",
            "bad": "#ff7b72" if self.theme_mode == "dark" else "#c42b1c",
            "muted": palette["muted"],
        }
        tone = tone_colors[overall["tone"]]
        left, right = scale(14), width - scale(14)
        summary_top, summary_bottom = scale(12), scale(82)
        canvas.create_polygon(
            rounded_box_points(
                left,
                summary_top,
                right,
                summary_bottom,
                scale(15),
            ),
            smooth=True,
            splinesteps=24,
            fill=blend_color(chart_background, tone, 0.10),
            outline=blend_color(chart_background, tone, 0.28),
            width=1,
        )
        canvas.create_text(
            left + scale(16),
            summary_top + scale(18),
            text=f"综合结论 · {overall['grade']}",
            anchor="w",
            fill=tone,
            font=self._font_heading,
        )
        availability = overall["availability"]
        p95 = overall["p95"]
        jitter = overall["jitter"]
        summary_metrics = (
            f"可用性 {'--' if availability is None else f'{availability:.0f}%'}"
            f"　P95 {'--' if p95 is None else f'{p95:.0f} ms'}"
            f"　抖动 {'--' if jitter is None else f'{jitter:.0f} ms'}"
        )
        canvas.create_text(
            right - scale(16),
            summary_top + scale(18),
            text=summary_metrics,
            anchor="e",
            fill=palette["text"],
            font=self._font_small,
        )
        canvas.create_text(
            left + scale(16),
            summary_top + scale(47),
            text=overall["conclusion"],
            anchor="w",
            fill=palette["muted"],
            font=self._font_default,
            width=max(scale(240), right - left - scale(32)),
        )

        table_top = scale(97)
        columns = (
            (left + scale(14), "站点", "w"),
            (left + (right - left) * 0.42, "最近响应", "center"),
            (left + (right - left) * 0.58, "P95", "center"),
            (left + (right - left) * 0.72, "成功率", "center"),
            (right - scale(18), "判断", "e"),
        )
        for column_x, label, anchor in columns:
            canvas.create_text(
                column_x,
                table_top,
                text=label,
                anchor=anchor,
                fill=palette["subtle"],
                font=self._font_small,
            )
        rows_top = table_top + scale(14)
        available_height = max(scale(80), height - rows_top - scale(10))
        row_height = min(
            scale(52),
            max(scale(38), available_height // max(1, len(rows))),
        )
        status_colors = {
            "流畅": tone_colors["good"],
            "可用": tone_colors["good"],
            "一般": tone_colors["warn"],
            "偏慢": tone_colors["warn"],
            "不稳定": tone_colors["warn"],
            "不可用": tone_colors["bad"],
            "失败": tone_colors["bad"],
            "卡顿": tone_colors["bad"],
            "等待": palette["subtle"],
        }
        for index, row in enumerate(rows):
            y1 = rows_top + index * row_height
            y2 = y1 + row_height - scale(5)
            canvas.create_polygon(
                rounded_box_points(left, y1, right, y2, scale(12)),
                smooth=True,
                splinesteps=20,
                fill=palette["surface"],
                outline=palette["border"],
                width=1,
            )
            latest = row["latest"]
            latest_note = "尚未采样"
            if latest is not None:
                if latest["status"] is not None:
                    latest_note = f"HTTP {latest['status']}"
                elif latest["error"]:
                    latest_note = str(latest["error"])
            center_y = (y1 + y2) / 2
            canvas.create_text(
                left + scale(14),
                center_y - scale(7),
                text=row["target"],
                anchor="w",
                fill=palette["text"],
                font=self._font_default,
            )
            canvas.create_text(
                left + scale(14),
                center_y + scale(10),
                text=f"{row['samples']} 次 · {latest_note}",
                anchor="w",
                fill=palette["subtle"],
                font=self._font_small,
            )
            latest_text = (
                "--" if row["latest_ms"] is None else f"{row['latest_ms']:.0f} ms"
            )
            p95_text = "--" if row["p95"] is None else f"{row['p95']:.0f} ms"
            success_text = (
                "--"
                if row["success_rate"] is None
                else f"{row['success_rate']:.0f}%"
            )
            for column_x, value in (
                (columns[1][0], latest_text),
                (columns[2][0], p95_text),
                (columns[3][0], success_text),
            ):
                canvas.create_text(
                    column_x,
                    center_y,
                    text=value,
                    fill=palette["text"],
                    font=self._font_default,
                )
            assessment = row["assessment"]
            canvas.create_text(
                columns[4][0],
                center_y,
                text=f"● {assessment}",
                anchor="e",
                fill=status_colors.get(assessment, palette["muted"]),
                font=self._font_default,
            )

    def _draw_chart(self) -> None:
        if self.chart_view_mode == "web":
            self._draw_web_analysis()
            return
        self.chart_title_var.set("实时趋势")
        canvas = self.chart
        canvas.delete("all")
        scale = self._scaled
        palette = self._theme_colors
        axis_text = palette["muted"]
        heading_text = palette["text"]
        grid_color = palette["grid"]
        chart_background = palette["surface_alt"]
        colors = (
            {
                "download": "#4aa8ff",
                "upload": "#46c15b",
                "latency": "#ff8a4c",
                "web": "#b58cff",
            }
            if self.theme_mode == "dark"
            else {
                "download": "#0067c0",
                "upload": "#107c10",
                "latency": "#d83b01",
                "web": "#744da9",
            }
        )
        width = max(canvas.winfo_width(), scale(300))
        height = max(canvas.winfo_height(), scale(170))
        supersample = 2
        render = Image.new(
            "RGB",
            (width * supersample, height * supersample),
            palette["surface"],
        )
        image_draw = ImageDraw.Draw(render)

        def hi(value: float) -> int:
            return round(value * supersample)

        image_draw.rounded_rectangle(
            (hi(1), hi(1), hi(width - 1), hi(height - 1)),
            radius=hi(scale(18)),
            fill=chart_background,
            outline=palette["border"],
            width=max(1, supersample),
        )

        def dashed_line(
            start: tuple[float, float],
            end: tuple[float, float],
            color: str,
            dash: int,
            gap: int,
        ) -> None:
            x1, y1 = start
            x2, y2 = end
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= 0:
                return
            unit_x = (x2 - x1) / length
            unit_y = (y2 - y1) / length
            position = 0.0
            while position < length:
                segment_end = min(length, position + dash)
                image_draw.line(
                    (
                        hi(x1 + unit_x * position),
                        hi(y1 + unit_y * position),
                        hi(x1 + unit_x * segment_end),
                        hi(y1 + unit_y * segment_end),
                    ),
                    fill=color,
                    width=max(1, supersample),
                )
                position += dash + gap

        left = scale(56)
        right = width - scale(18)
        top = scale(34)
        bottom = height - scale(34)
        split = top + int((bottom - top) * 0.62)
        speed_bottom = split - scale(14)
        latency_top = split + scale(27)

        samples = list(self.samples)
        visible = samples[-120:]

        def nice_axis_max(value: float, minimum: float) -> float:
            value = max(value, minimum)
            magnitude = 10 ** math.floor(math.log10(value))
            normalized = value / magnitude
            for step in (1.0, 2.0, 5.0, 10.0):
                if normalized <= step:
                    return step * magnitude
            return 10.0 * magnitude

        speed_peak = max(
            [sample[1] for sample in visible]
            + [sample[2] for sample in visible]
            + [10.0]
        )
        speed_max = nice_axis_max(speed_peak * 1.12, 10.0)
        latency_values = [sample[3] for sample in visible if sample[3] is not None]
        web_values = [sample[4] for sample in visible if sample[4] is not None]
        latency_peak = max(latency_values + web_values + [50.0])
        latency_max = nice_axis_max(latency_peak * 1.12, 50.0)
        x_count = max(len(visible) - 1, 1)

        def x(index: int) -> float:
            return left + (right - left) * index / x_count

        def y_speed(value: float) -> float:
            return speed_bottom - (speed_bottom - top) * value / speed_max

        def y_latency(value: float) -> float:
            return bottom - (bottom - latency_top) * value / latency_max

        for tick in range(5):
            fraction = tick / 4
            xx = left + (right - left) * fraction
            dashed_line(
                (xx, top),
                (xx, bottom),
                grid_color,
                scale(2),
                scale(5),
            )

        for area_top, area_bottom, maximum, label, unit, line_count in [
            (top, speed_bottom, speed_max, "传输速度", "Mbps", 4),
            (latency_top, bottom, latency_max, "网络响应", "ms", 3),
        ]:
            canvas.create_text(
                left,
                area_top - scale(10),
                text=label,
                anchor="sw",
                fill=heading_text,
                font=self._font_heading,
            )
            canvas.create_text(
                left + scale(62),
                area_top - scale(10),
                text=unit,
                anchor="sw",
                fill=axis_text,
                font=self._font_small,
            )
            for line in range(line_count):
                denominator = max(1, line_count - 1)
                yy = area_bottom - (area_bottom - area_top) * line / denominator
                value = maximum * line / denominator
                dashed_line(
                    (left, yy),
                    (right, yy),
                    grid_color,
                    scale(3),
                    scale(4),
                )
                canvas.create_text(
                    left - scale(9),
                    yy,
                    text=f"{value:.0f}",
                    anchor="e",
                    fill=axis_text,
                    font=self._font_small,
                )

        def area_gradient(
            points: list[float],
            baseline: float,
            color: str,
            strength: float,
        ) -> None:
            pairs = list(zip(points[0::2], points[1::2]))
            if len(pairs) < 2:
                return
            bands = 6
            for band in range(bands):
                upper_fraction = band / bands
                lower_fraction = (band + 1) / bands
                upper: list[float] = []
                lower: list[float] = []
                for px, py in pairs:
                    upper.extend(
                        (px, py + (baseline - py) * upper_fraction)
                    )
                for px, py in reversed(pairs):
                    lower.extend(
                        (px, py + (baseline - py) * lower_fraction)
                    )
                amount = strength * (1.0 - band / (bands + 1))
                image_draw.polygon(
                    [
                        (hi(px), hi(py))
                        for px, py in zip(
                            (upper + lower)[0::2],
                            (upper + lower)[1::2],
                        )
                    ],
                    fill=blend_color(chart_background, color, amount),
                )

        def line_with_glow(
            points: list[float],
            color: str,
            marker: bool = True,
        ) -> None:
            if len(points) < 4:
                return
            pairs = list(zip(points[0::2], points[1::2]))

            def catmull_rom(
                source: list[tuple[float, float]],
                steps: int = 7,
            ) -> list[tuple[float, float]]:
                if len(source) < 3:
                    return source
                result: list[tuple[float, float]] = []
                for index in range(len(source) - 1):
                    p0 = source[max(0, index - 1)]
                    p1 = source[index]
                    p2 = source[index + 1]
                    p3 = source[min(len(source) - 1, index + 2)]
                    for step in range(steps):
                        t = step / steps
                        t2 = t * t
                        t3 = t2 * t
                        px = 0.5 * (
                            2 * p1[0]
                            + (-p0[0] + p2[0]) * t
                            + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                            + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
                        )
                        py = 0.5 * (
                            2 * p1[1]
                            + (-p0[1] + p2[1]) * t
                            + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                            + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
                        )
                        result.append((px, max(1.0, min(height - 1.0, py))))
                result.append(source[-1])
                return result

            smooth_pairs = catmull_rom(pairs)
            high_points = [(hi(px), hi(py)) for px, py in smooth_pairs]
            image_draw.line(
                high_points,
                fill=blend_color(chart_background, color, 0.26),
                width=hi(scale(5)),
                joint="curve",
            )
            image_draw.line(
                high_points,
                fill=color,
                width=hi(scale(2)),
                joint="curve",
            )
            if marker:
                px, py = points[-2], points[-1]
                image_draw.ellipse(
                    (
                        hi(px - scale(5)),
                        hi(py - scale(5)),
                        hi(px + scale(5)),
                        hi(py + scale(5)),
                    ),
                    fill=blend_color(chart_background, color, 0.20),
                )
                image_draw.ellipse(
                    (
                        hi(px - scale(2)),
                        hi(py - scale(2)),
                        hi(px + scale(2)),
                        hi(py + scale(2)),
                    ),
                    fill=color,
                    outline=chart_background,
                    width=max(1, supersample),
                )

        def optional_segments(value_index: int) -> list[list[float]]:
            segments: list[list[float]] = []
            segment: list[float] = []
            for index, sample in enumerate(visible):
                value = sample[value_index]
                if value is None:
                    if len(segment) >= 4:
                        segments.append(segment)
                    segment = []
                else:
                    segment.extend((x(index), y_latency(float(value))))
            if len(segment) >= 4:
                segments.append(segment)
            return segments

        if len(visible) >= 2:
            download_points: list[float] = []
            upload_points: list[float] = []
            for index, (_, download_speed, upload_speed, _, _) in enumerate(visible):
                download_points.extend((x(index), y_speed(download_speed)))
                upload_points.extend((x(index), y_speed(upload_speed)))
            area_gradient(
                download_points,
                speed_bottom,
                colors["download"],
                0.16,
            )
            area_gradient(
                upload_points,
                speed_bottom,
                colors["upload"],
                0.13,
            )
            line_with_glow(download_points, colors["download"])
            line_with_glow(upload_points, colors["upload"])
            for segment in optional_segments(3):
                line_with_glow(segment, colors["latency"])
            for segment in optional_segments(4):
                line_with_glow(segment, colors["web"])

        if visible:
            start_elapsed = visible[0][0]
            end_elapsed = visible[-1][0]
            span = max(0.0, end_elapsed - start_elapsed)
            state_text = "实时采样" if self.running else "已停止"
            self.chart_subtitle_var.set(
                f"最近 {len(visible)} 个采样 · {span:.0f} 秒窗口 · {state_text}"
            )
            for tick in range(5):
                fraction = tick / 4
                sample_index = min(
                    len(visible) - 1,
                    round((len(visible) - 1) * fraction),
                )
                canvas.create_text(
                    left + (right - left) * fraction,
                    bottom + scale(19),
                    text=f"{visible[sample_index][0]:.0f}s",
                    anchor=("w" if tick == 0 else "e" if tick == 4 else "center"),
                    fill=axis_text,
                    font=self._font_small,
                )
        else:
            self.chart_subtitle_var.set(
                "最近 120 个采样 · "
                + ("正在建立时间序列" if self.running else "等待测试")
            )
            center_x = (left + right) / 2
            center_y = (top + bottom) / 2
            accent = colors["download"]
            canvas.create_oval(
                center_x - scale(24),
                center_y - scale(36),
                center_x + scale(24),
                center_y + scale(12),
                fill=blend_color(chart_background, accent, 0.12),
                outline="",
            )
            canvas.create_text(
                center_x,
                center_y - scale(12),
                text="↗",
                fill=accent,
                font=self._font_title,
            )
            canvas.create_text(
                center_x,
                center_y + scale(28),
                text="等待采样数据",
                fill=heading_text,
                font=self._font_heading,
            )
            canvas.create_text(
                center_x,
                center_y + scale(49),
                text="开始测试后将绘制传输速度与网络响应曲线",
                fill=axis_text,
                font=self._font_small,
            )
        final_render = render.resize(
            (width, height),
            Image.Resampling.LANCZOS,
        )
        self._chart_photo = ImageTk.PhotoImage(final_render, master=canvas)
        canvas.create_image(
            0,
            0,
            anchor="nw",
            image=self._chart_photo,
            tags="chart_bitmap",
        )
        canvas.tag_lower("chart_bitmap")

    def stop(self, completed: bool = False) -> None:
        if not self.running:
            return
        self.running = False
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.stop_event is not None:
            self.stop_event.set()
        if self.csv_file is not None:
            self.csv_file.close()
            self.csv_file = None
            self.csv_writer = None
        prefix = "测试完成" if completed else "测试已停止"
        self.session_var.set(prefix)
        self._set_main_controls_running(False)
        self.detail_var.set(f"{prefix} · 日志已保存到 {self.csv_path}")
        self._draw_chart()
        self._refresh_history()

    def close(self) -> None:
        self.stop()
        if self._chart_resize_after is not None:
            try:
                self.root.after_cancel(self._chart_resize_after)
            except tk.TclError:
                pass
            self._chart_resize_after = None
        if self._ip_resize_after is not None:
            try:
                self.root.after_cancel(self._ip_resize_after)
            except tk.TclError:
                pass
            self._ip_resize_after = None
        try:
            self.ip_info_var.trace_remove("write", self._ip_info_trace_id)
        except tk.TclError:
            pass
        if self._status_animation_after is not None:
            try:
                self.root.after_cancel(self._status_animation_after)
            except tk.TclError:
                pass
            self._status_animation_after = None
        self.root.destroy()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Realtime network stability GUI")
    parser.add_argument("--autostart", action="store_true", help="Start the test after opening")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enable_high_dpi()
    root = tk.Tk()
    NetworkTestApp(root, autostart=args.autostart)
    root.mainloop()


if __name__ == "__main__":
    main()
