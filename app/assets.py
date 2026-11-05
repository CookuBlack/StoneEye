# -*- coding: utf-8 -*-
"""程序图标 / 资源定位。

打包成 exe 后（PyInstaller）资源被解到 sys._MEIPASS，开发时就在项目根目录。
两种情况下都以「assets/app.ico」为准，只有图标文件缺失时才临时画一个兜底图标
（宁可丑一点，也不能让托盘出现空白格）。
"""
import os
import sys

_CACHE = {}


def resource_path(*parts) -> str:
    """相对项目根的路径 -> 绝对路径（打包后在临时解压目录里）。"""
    base = getattr(sys, "_MEIPASS", "")
    if not base:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def icon_path() -> str:
    for name in ("app.ico", "app.png"):
        path = resource_path("assets", name)
        if os.path.exists(path):
            return path
    return ""


def app_icon():
    """整个程序共用一个 QIcon（托盘、任务栏、面板标题栏）。"""
    if "icon" in _CACHE:
        return _CACHE["icon"]
    from PySide6.QtGui import QIcon

    path = icon_path()
    icon = QIcon(path) if path else _fallback_icon()
    _CACHE["icon"] = icon
    return icon


def app_pixmap(size: int):
    """取指定边长的图标位图，尽量命中 ico 里的原生尺寸，避免缩放发虚。"""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QPixmap

    icon = app_icon()
    if icon.isNull():
        return QPixmap()
    pm = icon.pixmap(QSize(int(size), int(size)))
    if pm.isNull():
        return QPixmap()
    # 高 DPI 下 pixmap 自带 DPR，绘制到固定尺寸的 QLabel 里会缩一圈，
    # 这里抹平，让调用方按物理像素算大小
    pm.setDevicePixelRatio(1.0)
    return pm


def _fallback_icon():
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2B2B2B"))
    p.drawRoundedRect(QRectF(1, 1, 62, 62), 14, 14)
    p.setBrush(QColor("#F2F2F2"))
    p.drawRoundedRect(QRectF(14, 18, 36, 3.5), 1.75, 1.75)
    p.setBrush(QColor("#4E8DFF"))
    p.drawRoundedRect(QRectF(14, 27, 26, 3.5), 1.75, 1.75)
    p.setBrush(QColor("#F0A020"))
    p.drawRoundedRect(QRectF(14, 36, 32, 3.5), 1.75, 1.75)
    p.end()
    return QIcon(pm)


__all__ = ["app_icon", "app_pixmap", "icon_path", "resource_path"]
