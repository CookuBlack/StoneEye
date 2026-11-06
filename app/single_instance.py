# -*- coding: utf-8 -*-
"""单实例守护：第二个 StoneEye.exe 不再开出第二个托盘。

两层机制，各管一件事：

1. **命名互斥体**（Windows kernel32 CreateMutexW）判定「我是不是第一个」。
   互斥体由进程持有，进程一退出系统自动释放，崩溃也不会留下残留锁文件。
   不能用「遍历进程列表」，那既慢又会被同名进程 / 权限问题坑到。
2. **QLocalServer / QLocalSocket** 让第二个实例给第一个捎个信：
   直接把已经在跑的那个的控制面板唤起来，用户不会以为「双击没反应」。
   Windows 上是命名管道，不需要额外的 socket 文件。

必须在创建 QApplication 之后、拉起托盘/OCR 线程之前调用 try_start()。
--selftest / --uninstall 在 main() 里更早分流，不走这里。
"""
import ctypes
import sys

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

SERVER_NAME = "StoneEye.SingleInstance.v1"
MUTEX_NAME = "StoneEye.SingleInstance.Mutex"
ERROR_ALREADY_EXISTS = 183

_mutex_handle = None     # 必须活到进程结束，绝不能让 GC 提前 CloseHandle


def _acquire_mutex() -> bool:
    """返回 True 表示抢到了（第一个实例），False 表示已经有人在跑。"""
    global _mutex_handle
    if sys.platform != "win32":
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        _mutex_handle = kernel32.CreateMutexW(None, 1, MUTEX_NAME)
        return ctypes.get_last_error() != ERROR_ALREADY_EXISTS
    except Exception:
        # 拿不到互斥体时宁可放行，也不能让程序彻底起不来
        return True


def _notify_existing(command: str = "activate") -> bool:
    """给已经在跑的实例发一条短命令；连不上就返回 False。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(600):
        return False
    try:
        sock.write(command.encode("utf-8"))
        sock.flush()
        sock.waitForBytesWritten(600)
    finally:
        sock.disconnectFromServer()
        sock.waitForDisconnected(300)
    return True


class SingleInstance(QObject):
    """第一个实例持锁并监听；第二个实例发完消息就退出。"""

    activated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._server = None
        self._sockets = []

    def try_start(self) -> bool:
        if not _acquire_mutex():
            # 已有实例：唤醒它的面板，然后让 main() 直接返回
            _notify_existing("activate")
            return False

        # 上次崩溃可能留下同名管道；Windows 上 removeServer 是安全的
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(SERVER_NAME):
            # 监听失败不影响主功能，只是二次启动没法唤起面板
            self._server = None
        return True

    def _on_connection(self):
        while self._server is not None and self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._sockets.append(sock)
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))

    def _on_ready(self, sock):
        try:
            data = bytes(sock.readAll()).decode("utf-8", "ignore").strip()
        except Exception:
            data = ""
        if data:
            self.activated.emit(data)
        try:
            sock.disconnectFromServer()
        except Exception:
            pass
        if sock in self._sockets:
            self._sockets.remove(sock)
