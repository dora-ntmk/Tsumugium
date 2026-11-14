# Tsumugium アーキテクチャ

Tsumugiumは、Discord固有の登録処理、ユースケース、状態、外部通信、DB操作を分離しています。表示言語は日本語へ固定しています。

## 全体構成

```mermaid
flowchart LR
    Discord["Discord Gateway / Slash Commands"]
    Cogs["cogs<br/>コマンド・イベント登録"]
    Message["MessageService"]
    Connection["ConnectionService"]
    Speech["SpeechService"]
    Voice["VoiceService"]
    Status["StatusService"]
    Session["GuildSession<br/>キュー・一時状態"]
    Swap["swap.py<br/>純粋なテキスト前処理"]
    Dictionary["DictManager"]
    ConfigRepo["GuildConfigRepository"]
    DictRepo["DictionaryRepository"]
    SoundRepo["SoundboardCacheRepository"]
    UserRepo["UserConfigRepository"]
    Voicevox["VoicevoxClient"]
    SoundClient["DiscordSoundboardClient"]
    ConfigDB[("config.db")]
    DictDB[("dict.db")]
    SoundDB[("soundboards.db")]
    UserDB[("users.db")]
    Engine["VOICEVOX ENGINE"]
    SoundAPI["Discord Soundboard API"]

    Discord --> Cogs
    Cogs --> Message
    Cogs --> Connection
    Cogs --> Voice
    Cogs --> Status
    Status --> Discord
    Message --> Connection
    Message --> Speech
    Connection --> Speech
    Connection --> Voice
    Speech --> Dictionary
    Dictionary --> DictRepo
    Dictionary --> Swap
    Speech --> Voicevox
    Speech --> Voice
    Voice --> Session
    Voice --> SoundClient
    ConfigRepo --> ConfigDB
    DictRepo --> DictDB
    SoundRepo --> SoundDB
    UserRepo --> UserDB
    Voicevox --> Engine
    SoundClient --> SoundAPI
```

この図の矢印は、主要な呼び出し・利用の方向を示します。戻り値による逆向きの矢印は省略し、処理中の往復は後述のシーケンス図で示します。

## レイヤーごとの責務

| レイヤー | 主な場所 | 責務 |
|---|---|---|
| Composition Root | `main.py` | オブジェクト生成、依存注入、Bot起動 |
| Discord登録層 | `cogs/`、`setting.py`、`word_dict.py`、`sound_dict.py` | コマンド・イベントを登録しService／Repositoryへ渡す |
| Service | `services/` | 接続、対象判定、音声生成判断、ユーザー読み方の解決、再生キュー管理 |
| Runtime Model | `models/` | キュー要素、ギルド単位状態、辞書スナップショット |
| Presentation | `presentation/` | 日本語Embedの共通ひな形とエラー応答 |
| Repository | `repositories/` | SQLiteの読み書きと既存スキーマ補完 |
| Client | `clients/` | VOICEVOX・Discord HTTP API、終了時のリソース解放 |
| Pure Processing | `swap.py` | DBやHTTPを持たないテキスト変換 |

依存方向は原則として、登録層 → Service → Repository / Client / Modelです。RepositoryとClientからDiscordコマンド層へは依存しません。

## メッセージから再生まで

読み上げの主要経路を示します。設定値の取得やエラー応答など、処理の理解に直接関係しない補助呼び出しは省略しています。

```mermaid
sequenceDiagram
    participant Discord
    participant Cog as PlaybackCog
    participant Message as MessageService
    participant Speech as SpeechService
    participant Dict as DictManager
    participant Repo as DictionaryRepository
    participant DB as dict.db
    participant Swap as swap.py
    participant VV as VoicevoxClient
    participant Voice as VoiceService

    Discord->>Cog: on_message
    Cog->>Message: handle(message)
    Message->>Message: Bot・チャンネル・スキップ判定
    Message->>Speech: add_message(message)
    Speech->>Dict: preprocess_text(...)
    Dict->>Repo: get_preprocessing_snapshot(guild_id)
    Repo->>DB: 辞書をSELECT
    DB-->>Repo: 辞書レコード
    Repo-->>Dict: DictionarySnapshot
    Dict->>Swap: preprocess_text(text, snapshot)
    Swap-->>Dict: PreprocessResult
    Dict-->>Speech: text, replaced_ranges, sound_id, spaced_out
    alt Soundboard条件に一致
        Speech->>Voice: enqueue(SoundboardItem)
        Voice->>Discord: Soundboard再生
    else TTS対象
        Speech->>Speech: 最大文字数処理
        Speech->>VV: generate(text)
        VV-->>Speech: WAVファイルパス
        Speech->>Voice: enqueue(TTSItem)
        Voice->>Discord: FFmpeg音声再生
    end
```

最大文字数の判定は`swap.py`の後に`SpeechService`が行います。優先辞書で保護された範囲の途中では切らず、その語の末尾まで残して「以下省略」を付けます。

空白区切りの同一文字反復と、異なる3文字以上の空白区切り部分列は文中でも連結します。メッセージ全文が異なる3文字以上を半角・全角スペース1つずつで区切った形式なら、連結後に`spaced_out=True`を設定します。通常文との混在時は連結だけを行い、`spaced_out=False`のまま通常の`Speed`を使用します。正規化はSoundboard辞書判定より前に行います。`SpeechService`はフラグ付き文章に`SpacedSpeed`を使用し、未設定（NULL）またはリセット後はデフォルト速度75を使用します。

### 音声生成・再生の一時障害

- 起動時はDiscord Gatewayへ接続する前にVOICEVOXの`/version`へHTTP疎通確認します。応答が得られない、2xx以外、または空の応答の場合は、起動中止理由をCLIへ1行で表示して終了します。
- `VoicevoxClient`は全ギルドの音声生成を1件ずつ処理します。タイムアウト・接続切断などの一時的な通信障害に限り、受付から最大30秒、0.5秒から最大5秒のバックオフを挟んで再試行します。一時切断後は所有するHTTPセッションを再生成し、HTTP 4xxなどの恒久エラーは再試行しません。
- `VoiceService`は再生中のTTSファイルを`GuildSession`で追跡します。`s`または`/clear instant`による意図的なFFmpeg停止はエラー通知せず、意図しない終了だけを通知します。

## 接続状態

```mermaid
stateDiagram-v2
    [*] --> Disconnected
    Disconnected --> Connected: /join・メンション・AutoJoin
    Connected --> Disconnected: /leave・メンション・AutoLeave
    Connected --> ForcedDisconnected: Discord側から強制切断
    ForcedDisconnected --> Connected: Discord再接続

    note right of Connected
      temporary_text_channel_idを使用
      keepaliveを実行
    end note
    note right of ForcedDisconnected
      pending_text_channel_idへ一時退避
      再接続時に復元
    end note
```

自発的な切断は`ConnectionService.voluntary_disconnects`で識別し、一時読み上げチャンネルを破棄します。

Botを止めるときは、`ReconnectService`がつないでいたVCを`data/state/voice-sessions.json`に書き残し、読み上げチャンネルへ知らせてからVCを抜けます。次に起動したとき、手で止めたのでなく3分以内なら、人がいるVCにつなぎ直します。止める理由は、インストーラーが`data/state/restart-reason.json`で渡します。

## Discordステータス

`StatusService`は`STATUS_MESSAGE`を起動時に検証し、`{voice_connections}`、`{voice_users}`、`{guilds}`を現在のDiscordキャッシュから展開します。起動時は即時反映し、VC状態または参加サーバーが変化した場合は連続イベントを1秒間まとめて更新します。展開結果が変わらない場合はDiscord APIを呼びません。

## DB境界と互換性

| DB | 所有Repository | 用途 |
|---|---|---|
| `config.db` | `GuildConfigRepository` | サーバー設定 |
| `dict.db` | `DictionaryRepository` | 読み辞書・音声辞書 |
| `soundboards.db` | `SoundboardCacheRepository` | Soundboard候補キャッシュ |
| `users.db` | `UserConfigRepository` | サーバー横断のユーザー読みと、準備領域であるユーザー別個人辞書 |

- v4では`guild_config`へ`SpacedSpeed`を追加し、前処理APIを`PreprocessResult`へ変更しています。v3へ戻す場合は移行前バックアップを使用してください。
- `guild_config.Language`は旧DB互換性のため残しますが、実行時には参照しません。
- 旧DBに不足する`Greeting`、`SpacedSpeed`、`full_match`、`trigger_user_id`は起動時に補完します。
- `SpacedSpeed`を追加する場合は、変更前の`config.db`を`backup_config_YYYYMMDD_HHMMSS_v3-latest.db`として先に保存します。バックアップに失敗した場合はDBを変更せず起動を中止し、移行前バックアップは通常ローテーションから除外します。
- Bot終了時は`ManagedDiscordClient`がHTTPセッションとSQLite接続を閉じます。
- `users.db`は起動時にテーブルを作成して定時バックアップ対象に含めます。`/user-reading`はギルドとDMの両方から本人の`reading`を更新し、常にephemeralで応答します。`UserReadingService`が保存済みの読み方をDiscordの表示名より優先する共通名前解決を提供し、メンションと入退室通知の双方から利用します。

## インストーラーと更新

サーバーへの導入と更新は`tsumugium-setup`（`installer/`）が行います。利用者向けの手順は[インストールと運用の手引き](INSTALL.md)、モジュールの一覧と決まりは[`AGENTS.md`](../AGENTS.md)にあります。

### 置き場所

```text
/opt/<インスタンス名>/
  .env          設定（root:<グループ>、640）
  app/          コードと専用のvenv（root。更新のたびに作り直す）
  app.prev/     1つ前のバージョン（rollbackで使う）
  data/         db/・backup/・tmp/・state/（専用ユーザー。Botが書き込めるのはここだけ）
/etc/tsumugium/<インスタンス名>.json   インストールで選んだ値の記録
/etc/systemd/system/<サービス名>.service
/usr/local/sbin/tsumugium-setup         入口（対象のインスタンスのapp/にあるインストーラーを呼ぶ）
```

DBの場所などはunitの`Environment=`で渡し、`.env`は`TSUMUGIUM_ENV_FILE`で指定してBotの`load_dotenv()`が読みます。`load_dotenv()`はすでにある環境変数を上書きしないので、unitの値が優先されます。`EnvironmentFile=`は使いません。systemdでは`EnvironmentFile=`の値が`Environment=`より優先され、`.env`に古いパスが残っているとunitの値が使われなくなるためです。

### 更新の流れ

```mermaid
sequenceDiagram
    participant Old as 今のバージョンのインストーラー（app/）
    participant New as 新しいバージョンのインストーラー（app.new/）
    participant Bot as Bot（systemd）
    Old->>Old: ロック、バージョンの選択、zipと.sha256の取得・照合、app.new/へ展開
    Old->>New: data/state/handoff.json（handoff: 1）で引き継ぐ
    New->>New: venvの作成、VOICEVOXの確認、.envへの書き足し
    New->>Bot: 停止
    New->>New: 記録に「更新中」、DBのバックアップ、app/→app.prev/、app.new/→app/、unitの書き換え
    New->>Bot: 起動
    Bot-->>New: data/state/ready.json（バージョンとプロセスID）
    New->>New: 記録のバージョンを書き換え、「更新中」を消す
```

- 今のバージョンは取得までしか行わず、それ以降は新しいバージョンのコードで行います。そのため、unitの形や記録の形式を変える更新も、新しいバージョンの知識で行えます。引き継ぐ形式には番号を付け、知らない番号を受け取ったバージョンは何も変えずに止まります。
- 停止より前（venvの作成、VOICEVOXの確認、`.env`の必須項目の確認）で止まったときは、`app.new/`を消すだけで、動いているBotには触れません。
- 停止のあとで失敗したとき（起動を60秒以内に確認できなかったときを含む）は、コード・venv・DB・unit・`app.prev/`を更新の前に戻し、前のバージョンで起動し直します。`.env`に書き足した項目は残します。
- 途中でプロセスが止まると、記録に「更新中」が残ります。次に実行したときに、`repair`か`rollback`で直すように求めます。

### repairとrollback

- `repair`は、記録のバージョンを取得し直し、`upgrade`と同じ流れで入れ直します。`app.prev/`は残し、元の`app/`は成功するまで`app.repair-old/`に置いておきます。
- `rollback`は、入口が`app.prev/`にある1つ前のバージョンのインストーラーで動かします。`app/`と`app.prev/`を入れ替え、そのバージョンから更新したときのDBのバックアップに戻すかを聞きます（既定は戻す）。unitは戻す先のバージョンの形で書き直します。

### 自動更新

```mermaid
sequenceDiagram
    participant Timer as <サービス名>-update.timer（10分おき）
    participant Cur as 今のバージョンのインストーラー（app/）
    participant GH as GitHub Releases
    Timer->>Cur: tsumugium-setup upgrade --auto
    Cur->>Cur: オフ・Botを手で停止・更新が途中・ロック中なら、理由をauto-update.jsonに残して終わる
    Cur->>GH: チャンネルの最新のバージョン
    Cur->>Cur: 今より新しくない、または「飛ばすバージョン」なら終わる
    Cur->>Cur: 「更新の流れ」と同じく取得・引き継ぎ（handoff.jsonに auto: true）
```

- serviceは、今の`app/`に`installer/autoupdate.py`があるときだけ動きます（`ConditionPathExists`）。自動更新のないバージョンに`rollback`したときに、10分おきに失敗し続けないようにするためです。
- 新しいバージョンのインストーラーは、更新・入れ直し・ロールバックの結果を`data/state/last-update.json`に書きます（手で更新したときも書きます）。起動を確かめられずに戻したバージョンは、記録の`skip_versions`に入れ、自動更新が同じバージョンを何度も入れ直さないようにします。`rollback`でも、戻した元のバージョンを入れます。
- 更新しているあいだは`data/state/updating.json`を置き、Botはそれを見てステータスを「アップデート中」にします。Botは`last-update.json`を見張り、新しい結果が書かれたら運営者へDMで知らせます（`services/update_watch_service.py`）。
- 引き継ぎに足した`auto`は、知らないバージョンが読み飛ばしても動きが変わらないので、引き継ぎの番号は上げていません。

## 変更時の注意点

- `swap.py`の前処理順序を変更する場合は、優先辞書・URL・カスタム絵文字・`www`の回帰テストを必ず実行してください。
- 優先辞書は前処理順を制御する内部概念です。利用者向けの辞書一覧では通常辞書と区分せず、登録・更新日時の降順で表示します。
- `GuildSession`以外へギルド単位の一時状態を増やさないでください。
- SQLをServiceやCogへ直接追加せず、対応するRepositoryへ置いてください。
- HTTP URL・認証ヘッダーをServiceへ直接追加せず、対応するClientへ置いてください。
- Embed本文は利用箇所でf-string等を使って組み立て、共通の外形だけを`make_embed()`へ任せてください。
