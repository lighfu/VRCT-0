"""重いモジュールが mainloop の import だけでは読み込まれないこと。

別プロセスで `import mainloop` だけを行い、sys.modules を調べる。
"""

import json
import os
import subprocess
import sys
import unittest

_SRC_PYTHON = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# Step 1 の計測 (docs/perf/README.md 「遅延 import 後（CPU）」参照) で選んだモジュール。
#
# ctranslate2 は候補から除外した: config.py の Config.init_config() が
# (config シングルトン構築時、つまり `import mainloop` の時点で既に)
# utils.getComputeDeviceList() 経由で ctranslate2.get_supported_compute_types /
# get_cuda_device_count を呼び、SELECTABLE_COMPUTE_DEVICE_LIST と
# _COMPUTE_MODE (cpu/cuda 初期値) を決めている。さらに controller.init() も
# 起動直後に model.checkTranslatorCTranslate2ModelWeight() を呼ぶ。
# どちらも「起動時に必ず使う」ため、CodeGraph で辿った上で対象から外した
# (utils.py 側の _ct2_get_supported_compute_types/_ct2_get_cuda_device_count
# 自体は import を遅延させてあり、翻訳の実処理では初回使用まで読み込まない)。
#
# openvr も候補から除外した: openvr 単体の累積 import 時間は約11msで
# 50ms未満 (OpenGL/glfw/cv2/onnxruntime を道連れにしていたのは
# models/ocr/ocr_capture_openvr.py 側だけで、そちらは遅延済み)。
# models/overlay/overlay.py 等の openvr_session.acquire() はデフォルト引数に
# openvr.VRApplication_Background を使っており、遅延させるには関数シグネチャの
# 変更が要る (単純な import 移動を超える)。閾値未満なので対象外とした。
_LAZY_MODULES = [
    "faster_whisper",
    "sherpa_onnx",
    "translators",
    "google.genai",
    "openai",
    "rapidocr",
    "glfw",
    "OpenGL",
]


class LazyImportTests(unittest.TestCase):
    def test_heavy_modules_are_not_loaded_at_startup(self) -> None:
        code = (
            "import sys, json; import mainloop; "
            f"print(json.dumps([m for m in {_LAZY_MODULES!r} if m in sys.modules]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=_SRC_PYTHON,
            capture_output=True, text=True, encoding="utf-8", timeout=300,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        loaded = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, [])


if __name__ == "__main__":
    unittest.main()
