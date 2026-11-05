# -*- coding: utf-8 -*-
"""把 dist/StoneEye 压成安装程序要用的 payload.zip。

为什么要先压好、而不是让 PyInstaller 直接把整个文件夹当 data 打进去：
  - 单文件包里每个文件是单独压缩的，压不动模型和 DLL 之间的大段冗余；
    先打一个 zip 至少能吃到一份完整的 deflate；
  - 安装时要往目标目录铺 300+ 个文件，直接从 zip 里按条目解压比
    让 _MEIPASS 里那堆散文件再拷一遍快，也不会在临时目录多存一份。
"""
import json
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "dist", "StoneEye")
OUT_DIR = os.path.join(ROOT, "installer")
OUT_ZIP = os.path.join(OUT_DIR, "payload.zip")
OUT_INFO = os.path.join(OUT_DIR, "payload_info.json")


SKIP_NAMES = {"selftest.log", "selftest.png", "error.log", "uninstall.exe"}


def main() -> int:
    if not os.path.isdir(SRC):
        print("[ERROR] 找不到 %s\n        先跑 build.bat onedir 出一份文件夹版。" % SRC)
        return 1

    sys.path.insert(0, ROOT)
    try:
        from app import __version__
    except Exception:
        __version__ = "0.0.0"

    entries = []
    for base, dirs, files in os.walk(SRC):
        dirs.sort()
        for name in sorted(files):
            if name in SKIP_NAMES:
                continue
            path = os.path.join(base, name)
            entries.append((path, os.path.relpath(path, SRC)))

    total = sum(os.path.getsize(p) for p, _ in entries)
    print("打包 %d 个文件 / %s -> %s" % (len(entries), _human(total), OUT_ZIP))

    os.makedirs(OUT_DIR, exist_ok=True)
    started = time.time()
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, (path, arc) in enumerate(entries, 1):
            zf.write(path, arc.replace(os.sep, "/"))
            if i % 40 == 0 or i == len(entries):
                print("  %d/%d" % (i, len(entries)), flush=True)

    packed = os.path.getsize(OUT_ZIP)
    info = dict(version=__version__, files=len(entries), bytes=total,
                packed=packed, built=time.strftime("%Y-%m-%d %H:%M:%S"))
    with open(OUT_INFO, "w", encoding="utf-8") as fp:
        json.dump(info, fp, ensure_ascii=False, indent=2)

    print("完成：%s（%s，压缩率 %.0f%%，用时 %.1fs）"
          % (_human(packed), human_diff(total, packed),
             packed * 100.0 / max(1, total), time.time() - started))
    return 0


def human_diff(raw: int, packed: int) -> str:
    return "原始 %s" % _human(raw)


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f GB" % n


if __name__ == "__main__":
    sys.exit(main())
