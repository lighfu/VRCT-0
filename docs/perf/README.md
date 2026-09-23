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

## transformers 撤去後（Task 7, 2026-09-23）

CTranslate2 のトークナイザを sentencepiece 直呼びの `CT2Tokenizer`（Task 6）に差し替え、
`transformers` をアプリ本体の依存から撤去した。`transformers` は
`requirements-dev.txt` にトークナイザ照合テスト専用として残る（実行時には未インストール）。
`bat\build.bat` は `VRCT_PYINSTALLER_CLEAN=1` を付けて実行した（`.venv` に
`transformers` が残っていた状態でキャッシュ済みビルドを流すと、PyInstaller が
古い Analysis キャッシュをそのまま使って `transformers` を巻き込んでしまうため）。

| 版 | bin 合計 | インストーラー | 起動（中央値） | アイドル RSS | モデル読込後 RSS |
|---|---|---|---|---|---|
| transformers 撤去後（CPU） | 1170.3MB | -（Rust 未導入のため未計測） | 5.32秒 | 222.5MB | - |

import 時間の上位 5（CPU 版）: mainloop: 3084.2ms, controller: 3059.1ms, model: 2111.9ms, models.translation.translation_translator: 1563.9ms, models.translation.translation_providers: 959.3ms
