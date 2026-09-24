# -*- mode: python ; coding: utf-8 -*-

import os

# UPX compression roughly doubles the PyInstaller collect step and adds
# a few seconds to sidecar startup. Default to disabled so ordinary
# rebuilds are fast; release scripts can opt in by setting
# VRCT_PYINSTALLER_UPX=1.
_use_upx = os.environ.get("VRCT_PYINSTALLER_UPX") == "1"


a = Analysis(
    ['..\\src-python\\mainloop.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('./../src-python/models/overlay/fonts', 'fonts/'),
        ('./../src-python/models/translation/translation_settings/prompt', 'translation_settings/prompt/'),
        ('./../src-python/models/translation/translation_settings/languages', 'translation_settings/languages/'),
        ('./../src-python/models/ocr/onnx', 'ocr_onnx/'),
        ('./../.venv/Lib/site-packages/zeroconf', 'zeroconf/'),
        ('./../.venv/Lib/site-packages/openvr', 'openvr/'),
        ('./../.venv/Lib/site-packages/faster_whisper', 'faster_whisper/'),
        ('./../.venv/Lib/site-packages/hf_xet', 'hf_xet/'),
        ('./../.venv/Lib/site-packages/rapidocr', 'rapidocr/'),
        ],
    hiddenimports=['faster_whisper.vad', 'rapidocr', 'cv2', 'OpenGL', 'glfw', 'models.ocr'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # transformers/torch: ctranslate2.converters.__init__ imports
    # ctranslate2.converters.transformers, which does `import transformers`
    # inside try/except ImportError. PyInstaller's modulegraph follows that
    # import (and pyinstaller-hooks-contrib ships hook-transformers.py), so
    # whenever transformers is installed in .venv (it is, via
    # requirements-dev.txt, for the tokenizer parity test) a normal build
    # bundles it (~37MB) even though the app itself no longer imports it
    # (2026-09-23, Task 7 fix round 1). ctranslate2 swallows the resulting
    # ImportError at runtime, so excluding both is safe.
    excludes=['pandas', 'matplotlib', 'PyQt5', 'transformers', 'torch'],
    noarchive=False,
    optimize=0,
)
# pyinstaller-hooks-contrib ships hook-sudachipy.py, which collects
# sudachidict_full's data files (the ~1GB full dictionary) whenever the
# sudachidict_full package happens to be importable in the build venv --
# regardless of whether the app imports it, and regardless of the
# `excludes=` list above (excludes only stops modulegraph from following
# an import; it does not stop a hook's collect_data_files() call). The
# full dictionary is meant to be an optional user download, not something
# bundled into the installer, so filter its TOC entries out here as a
# second line of defense against a stale/dirty .venv. TOC entries are
# (dest_name, src_name, typecode) tuples (PyInstaller 6.10).
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("sudachidict_full")]
a.binaries = [b for b in a.binaries if not b[0].replace("\\", "/").startswith("sudachidict_full")]

# rapidocr/models: bundle only the models the rapidocr wheel itself ships
# (PP-OCRv6 small det/rec + the cls model). rapidocr downloads other models
# (PP-OCRv5 per-script ones: Korean, Thai, ...) into this same folder of the
# build venv the first time a developer uses them, and without this filter a
# local `npm run release` would bundle whatever happens to be there while a
# clean CI build would not. The installed app downloads missing models into
# PATH_DATA/weights/rapidocr instead (models/ocr/ocr_engine_rapidocr.py).
from importlib.metadata import files as _dist_files
_rapidocr_wheel_models = {
    str(f).replace("\\", "/") for f in (_dist_files("rapidocr") or [])
    if str(f).replace("\\", "/").startswith("rapidocr/models/")
}
a.datas = [
    d for d in a.datas
    if not d[0].replace("\\", "/").startswith("rapidocr/models/")
    or d[0].replace("\\", "/") in _rapidocr_wheel_models
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VRCT-sidecar-x86_64-pc-windows-msvc',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=_use_upx,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=_use_upx,
    upx_exclude=[],
    name='.',
)
