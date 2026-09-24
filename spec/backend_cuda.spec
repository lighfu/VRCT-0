# -*- mode: python ; coding: utf-8 -*-

import os

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
        ('./../.venv_cuda/Lib/site-packages/zeroconf', 'zeroconf/'),
        ('./../.venv_cuda/Lib/site-packages/openvr', 'openvr/'),
        ('./../.venv_cuda/Lib/site-packages/faster_whisper', 'faster_whisper/'),
        # SenseVoice: keep sherpa_onnx/lib together so _sherpa_onnx.pyd finds
        # its onnxruntime.dll / sherpa-onnx-c-api.dll next to it.
        ('./../.venv_cuda/Lib/site-packages/sherpa_onnx', 'sherpa_onnx/'),
        ('./../.venv/Lib/site-packages/hf_xet', 'hf_xet/'),
        ('./../.venv_cuda/Lib/site-packages/rapidocr', 'rapidocr/'),
        ],
    # nvidia.cublas / nvidia.cudnn は ctranslate2 が GPU 実行時に
    # LoadLibrary で遅延ロードするDLLの提供元で、Python からは import
    # されないので依存解析に掛からない。ここで明示して
    # pyinstaller-hooks-contrib の hook-nvidia.* に _internal/nvidia/<lib>/bin/
    # へ収集させる (2026-09-18 に torch を落とすまでは、torch が同梱していた
    # 同じDLL群が torch 経由で収集されていた)。実行時のDLL検索パス登録は
    # src-python/utils.py の _registerBundledCudaLibraries が行う。
    hiddenimports=['faster_whisper.vad', 'sherpa_onnx', 'rapidocr', 'cv2', 'OpenGL', 'glfw', 'models.ocr',
                   'nvidia.cublas', 'nvidia.cudnn'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # transformers/torch: see spec/backend.spec for why these are excluded
    # (ctranslate2.converters optionally imports transformers; PyInstaller
    # follows that import and bundles it if installed in .venv_cuda).
    excludes=['pandas', 'matplotlib', 'PyQt5', 'transformers', 'torch'],
    noarchive=False,
    optimize=0,
)
# See spec/backend.spec for why this filter is needed: pyinstaller-hooks-
# contrib's hook-sudachipy.py collects sudachidict_full's data files
# whenever the package is importable in the build venv, bypassing
# `excludes=`. TOC entries are (dest_name, src_name, typecode) tuples
# (PyInstaller 6.10).
a.datas = [d for d in a.datas if not d[0].replace("\\", "/").startswith("sudachidict_full")]
a.binaries = [b for b in a.binaries if not b[0].replace("\\", "/").startswith("sudachidict_full")]

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
