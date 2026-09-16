# -*- mode: python ; coding: utf-8 -*-
# pyinstaller --noconfirm --clean build.spec  →  dist\HydrogelStudio_4F.exe
a = Analysis(["main.py"], pathex=[], binaries=[], datas=[], hiddenimports=[],
             hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="HydrogelStudio_4F", debug=False, bootloader_ignore_signals=False,
          strip=False, upx=False, console=False, disable_windowed_traceback=False)
