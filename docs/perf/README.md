# 軽量化の計測記録

A-1（Python サイドカーのスリム化）の計測結果を置く。
計測方法は `tools/measure_footprint.py` の docstring を参照。

## 作業開始時のテスト結果（2026-09-23）

- pytest: 936 passed, 43 warnings in 30.34s
- 既知の失敗: なし

## 基準値（作業前）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| CPU | 1186.9MB | -（Rust 未導入のため未計測） | 6.72秒 | 264.6MB | - |
| CUDA | 2950.8MB | -（Rust 未導入のため未計測） | 7.80秒 | 409.3MB | - |

import 時間の上位 5（CPU 版）: mainloop: 4978.2ms, controller: 4956.6ms, model: 3976.1ms, models.translation.translation_translator: 3271.8ms, models.translation.translation_providers: 2344.1ms

## langchain 撤去後（Task 5, 2026-09-23）

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| langchain 撤去後（CPU） | 1180.6MB | -（Rust 未導入のため未計測） | 4.70秒 | 220.5MB | - |

import 時間の上位 5（CPU 版）: mainloop: 2684.6ms, controller: 2667.1ms, model: 1808.5ms, models.translation.translation_translator: 1378.1ms, models.translation.translation_providers: 864.2ms

## transformers 撤去後（Task 7, 2026-09-23。fix round 1 で計測をやり直し）

CTranslate2 のトークナイザを sentencepiece 直呼びの `CT2Tokenizer`（Task 6）に差し替え、
`transformers` をアプリ本体の依存から撤去した。`transformers` は
`requirements-dev.txt` にトークナイザ照合テスト専用として残るため、通常の開発環境の
`.venv` には常にインストールされている。

`ctranslate2/__init__.py` が無条件に `ctranslate2.converters` を import し、
その `__init__.py` が `try/except ImportError` 越しに `import transformers` する
(pyinstaller-hooks-contrib の `hook-transformers.py` も効く)。PyInstaller の
modulegraph はこの try/except 内の import も辿るため、`.venv`/`.venv_cuda` に
`transformers` さえ入っていれば `VRCT_PYINSTALLER_CLEAN=1` の有無に関わらず
毎回ビルドに巻き込まれる（fix round 1 以前の記述にあった「キャッシュが古いせい」
は誤りで、原因は spec 側で除外していなかったこと）。
`spec/backend.spec` / `spec/backend_cuda.spec` の `excludes` に `transformers` と
`torch` を追加して対処した（ctranslate2 側は ImportError を握りつぶすだけなので
実行時に影響しない）。

以下は、通常の開発者 `.venv`（`requirements-dev.txt` 込みで `transformers` が
入った状態）から `bat\build.bat`（`VRCT_PYINSTALLER_CLEAN=1` 付きで 1 回）を
実行し、`src-tauri\bin\_internal` に `transformers`/`torch` ディレクトリが
無いことを確認した上で計測した値。

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| transformers 撤去後（CPU） | 1171.1MB | -（Rust 未導入のため未計測） | 5.77秒 | 223.2MB | - |

import 時間の上位 5（CPU 版）: mainloop: 3289.4ms, controller: 3269.8ms, model: 2341.4ms, models.translation.translation_translator: 1664.2ms, models.translation.translation_providers: 1020.0ms
