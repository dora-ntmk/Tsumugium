# Tsumugium

VOICEVOXを利用してDiscordのテキストチャンネルを読み上げるBotです。

## 主な機能

- ボイスチャンネルへの手動接続・自動接続
- 指定テキストチャンネルとVC付属チャットの読み上げ
- サーバー別の読み辞書・共通辞書
- Discord Soundboardを利用した音声辞書
- 話者・音量・速度・最大文字数のサーバー別設定
- 空白区切りの文章（例：「あ い う え お」）を専用の速度で読み上げ
- サーバー横断のユーザー読み方設定（`/user-reading`）
- スタンプ名・転送メッセージの読み上げ
- VCの入退室読み上げ通知
- 接続状況を表示するDiscordステータス
- 運営者へのエラー通知DM（任意）
- SQLiteデータベースの定時バックアップ

## 必要なもの

- Python 3.10以上
- FFmpeg実行ファイル
- VOICEVOX ENGINE、またはVOICEVOX互換HTTP API
- Discord Botトークン

FFmpegはPythonパッケージではなく、`ffmpeg`コマンドを実行できる状態である必要があります。
Bot起動時にVOICEVOXへのHTTP疎通確認を行うため、先にVOICEVOX ENGINEを起動してください。疎通できない場合、Botは起動しません。
旧`config.db`へ新しい設定カラムを追加する場合は、変更前に`BACKUP_DIR`へ末尾が`v3-latest`の移行前バックアップを自動保存します。バックアップに失敗した場合、DBを変更せず起動を中止します。

## セットアップ

入れ方は2つあります。

- **インストーラーで入れる（Ubuntu 22.04以上・Debian 12以上）**: 専用ユーザー・専用のvenv・systemdのサービスを作り、更新と1つ前のバージョンへの戻し方もそろえます。サーバーで動かすときは、こちらを使ってください。手順は[インストールと運用の手引き](docs/INSTALL.md)にあります。
- **手で動かす**: 開発するときや、ほかのOSで動かすときの方法です。以下に手順を書きます。

### 手で動かす

依存パッケージをインストールします。

```bash
python -m pip install -r requirements.txt
```

`.env.template`を`.env`へコピーし、最低限`DISCORD_BOT_TOKEN`を設定します。ほかの項目の意味は`.env.template`に書いてあります。

```dotenv
DISCORD_BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
OPERATOR_USER_ID=
DEFAULT_SPEAKER=8
STATUS_MESSAGE="{voice_connections}/{guilds}個のVCに接続中"
```

`STATUS_MESSAGE`では、`{voice_connections}`（Botの接続VC数）、`{voice_users}`（接続先VCにいる人間ユーザー数）、`{guilds}`（参加サーバー数）を利用できます。波括弧自体を表示する場合は`{{`または`}}`と記述します。未知の変数や不正な構文がある場合、設定エラーとしてBotは起動しません。

`OPERATOR_USER_ID`へDiscordユーザーIDを設定すると、Botで発生したエラーをそのユーザーへDMで通知します。空欄の場合は通知しません。

DBのバックアップは既定で毎日12:00に`BACKUP_DIR`へ取ります。時刻は`BACKUP_TIMES`で変えられ、空欄にするとバックアップを取りません。

VOICEVOX ENGINEを起動してからBotを起動します。

```bash
python main.py
```

DBファイル（`config.db`、`dict.db`、`soundboards.db`、`users.db`）、音声一時ディレクトリ（`TMP_DIR`）、バックアップディレクトリ（`BACKUP_DIR`）は必要に応じて自動作成されます。
DBファイルの親ディレクトリは自動作成されないため、`db/`以外の場所を指定する場合は事前にディレクトリを作成してください。

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
- 埋め込みリンク
- サウンドボードを使用

「埋め込みリンク」は、メンションによる接続・切断や自動接続・自動退出の通知をEmbedで送信するために必要です。
音声辞書（Soundboard）はDiscordの仕様上、Botがサーバーミュート・サーバースピーカーミュートされていると再生できません。ステージチャンネルで聴衆扱いになっている場合も同様です。

Privileged Gateway Intentsは、Message Content Intentのみ有効にしてください。Server Members IntentとPresence Intentは不要です。Soundboardの追加・更新・削除イベントは特権でないインテントで受信します。

## ドキュメント

- [ユーザーガイド](USERGUIDE.md)
- [インストールと運用の手引き](docs/INSTALL.md)
- [アーキテクチャと処理フロー](docs/ARCHITECTURE.md)
- [リリース確認手順](docs/RELEASE_CHECKLIST.md)
- [開発者向け仕様書](AGENTS.md)

v4では`config.db`へ`SpacedSpeed`カラムを、新たに`users.db`を追加しています。旧DBは起動時に自動で補完され、`config.db`の変更前には移行前バックアップを保存します。

## ライセンス

MIT License

## 使用OSS

- [emoji-ja](https://github.com/yagays/emoji-ja) - MIT License, Copyright 2018 yag_ays
