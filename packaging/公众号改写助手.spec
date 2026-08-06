# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


project_root = Path.cwd()
datas = [
    (str(project_root / "app" / "render" / "templates"), "app/render/templates"),
    (str(project_root / "frontend" / "dist"), "app/frontend/dist"),
]
binaries = []
hiddenimports = collect_submodules("app")
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("lark_oapi")
hiddenimports += collect_submodules("psycopg")

for package in (
    "trafilatura",
    "readability",
    "justext",
    "lark_oapi",
    "google.genai",
    "psycopg",
    "psycopg_binary",
):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

a = Analysis(
    [str(project_root / "app" / "launcher.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="公众号改写助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="公众号改写助手",
)
