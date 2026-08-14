# Tsumugium

VOICEVOXを利用してDiscordのテキストチャンネルを読み上げるBotです。

現在のバージョンは **v3.5.2** です。Botが送信するメッセージとコマンド説明は日本語固定です。

## 主な機能

- ボイスチャンネルへの手動接続・自動接続
- 指定テキストチャンネルとVC付属チャットの読み上げ
- サーバー別の読み辞書・共通辞書
- Discord Soundboardを利用した音声辞書
- 話者・音量・速度・最大文字数のサーバー別設定
- VCの入退室読み上げ通知
- SQLiteデータベースの定時バックアップ

## 必要なもの

- Python 3.10以上
- FFmpeg実行ファイル
- VOICEVOX ENGINE、またはVOICEVOX互換HTTP API
- Discord Botトークン

FFmpegはPythonパッケージではなく、`ffmpeg`コマンドを実行できる状態である必要があります。

## セットアップ

依存パッケージをインストールします。

```bash
python -m pip install -r requirements.txt
```

`.env.template`を`.env`へコピーし、最低限`DISCORD_BOT_TOKEN`を設定します。

```dotenv
DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
VOICEVOX_URL=http://127.0.0.1:50021
DEFAULT_SPEAKER=8
STATUS_MESSAGE="{voice_connections}/{guilds}個のVCに接続中"
```

`STATUS_MESSAGE`では、`{voice_connections}`（Botの接続VC数）、`{voice_users}`（接続先VCにいる人間ユーザー数）、`{guilds}`（参加サーバー数）を利用できます。波括弧自体を表示する場合は`{{`または`}}`と記述します。未知の変数や不正な構文がある場合、設定エラーとしてBotは起動しません。

VOICEVOX ENGINEを起動してからBotを起動します。

```bash
python main.py
```

DBファイルや音声一時ディレクトリは、設定されたパスへ必要に応じて自動作成されます。

## Discord側の設定

Developer Portalで以下のスコープを有効にします。

- `applications.commands`
- `bot`

主に必要な権限は以下です。

- チャンネルを見る
- メッセージを送信
- メッセージ履歴を読む
- ボイスチャンネルへ接続
- 発言
- サウンドボードを使用

Message Content Intentも有効にしてください。

## ドキュメント

- [ユーザーガイド](USERGUIDE.md)
- [アーキテクチャと処理フロー](docs/ARCHITECTURE.md)
- [リリース確認手順](docs/RELEASE_CHECKLIST.md)
- [開発者向け仕様書](AGENTS.md)

v3.5は内部構造を大きく整理していますが、v3系のSQLite DB構造を維持しています。`Language`カラムも旧DBとの互換性のため残りますが、表示言語は日本語固定です。

## ライセンス

MIT License

## 使用OSS

- [emoji-ja](https://github.com/yagays/emoji-ja) - MIT License, Copyright 2018 yag_ays
