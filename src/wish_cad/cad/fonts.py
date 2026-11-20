"""Resolve one user-installed CJK font for CAD text and the printed title block."""

from functools import lru_cache
import os
from pathlib import Path


@lru_cache(maxsize=1)
def selected_font():
    configured = os.environ.get("WISH_CAD_FONT")
    if configured:
        path = Path(configured).expanduser().resolve()
        if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
            return path
        raise ValueError("WISH_CAD_FONT must point to an existing .ttf, .otf or .ttc font.")

    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("simhei.ttf", "msyh.ttc"):
        path = windows_fonts / name
        if path.is_file():
            return path

    from matplotlib import font_manager

    installed = {Path(p).name.casefold(): Path(p) for p in sorted(font_manager.findSystemFonts())}
    for name in (
        "notosanscjksc-regular.otf",
        "sourcehansanscn-regular.otf",
        "sourcehansanssc-regular.otf",
        "notosanscjk-regular.ttc",
    ):
        if name in installed:
            return installed[name]
    raise ValueError("Install a CJK font or set WISH_CAD_FONT to its file path.")
