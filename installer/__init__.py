"""Tsumugium のインストーラー兼アップデーター。

標準ライブラリだけで動き、Python 3.10以上を前提にする。
初回は展開したzipの install.sh から、2回目以降は tsumugium-setup から呼ばれる。
"""

# 古いバージョンのインストーラーから新しいバージョンへ処理を引き継ぐときの形式の番号。
HANDOFF_VERSION = 1
