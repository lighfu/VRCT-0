"""
ビルドチャンネル定義

リリースのタグと合っているかを CI (.github/workflows/release.yml) が確かめる。
(元の VRCT ではテレメトリの送り先の切り替えにも使っていたが、VRCT-0 は
テレメトリを送らない。)
develop ブランチではこの値を "beta" のまま維持し、master へマージ/
チェリーピックする際にこの1行だけを "stable" に変更する。
"""
BUILD_CHANNEL = "beta"  # "stable" | "beta"
