# Tsumugium — Discord 読み上げBot 仕様書

VOICEVOXを使ったDiscordテキスト読み上げBot。

全体の依存関係と処理フローは[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)を参照。

---

## モジュール構成

| ファイル | 役割 |
|---|---|
| `main.py` | エントリーポイント。Client・Repository・Service・Cogの生成と依存注入、Bot起動のみを担当 |
| `services/message_service.py` | メッセージの対象チャンネル判定、Bot投稿、特殊トリガーを担当。接続操作は`ConnectionService`へ委譲 |
| `services/connection_service.py` | `/join`・`/leave`・単体メンション接続、AutoJoin、AccessNotice、強制切断後の状態復元を担当 |
| `services/speech_service.py` | テキスト前処理、Soundboard/TTS選択、最大文字数処理、VOICEVOX生成依頼を担当 |
| `services/voice_service.py` | ギルド別キュー、再生、スキップ、クリア、キープアライブを担当。外部API通信はClientへ委譲 |
| `services/error_notification_service.py` | エラーのCLI出力と、任意設定された運営者へのDiscord Embed形式DM通知を担当 |
| `services/status_service.py` | STATUS_MESSAGEの検証・変数展開と、接続状態に応じたDiscordステータス更新を担当 |
| `services/user_reading_service.py` | ユーザー設定の読み方を優先し、未設定時はDiscord表示名へ戻す共通名前解決を担当 |
| `services/dictionary_transfer_service.py` | 読み辞書をShovel形式（1行に1件の`単語, 読み`）のテキストへ書き出し、読み込む純粋な処理。SQLiteとDiscordは扱わない |
| `services/ready_state_service.py` | 起動の準備が終わったことを、インストーラーへ`ready.json`で知らせる。`UPDATE_STATE_DIR`が空欄なら何もしない |
| `cogs/connection_cog.py` | `/join`・`/leave`とVC状態イベントを`ConnectionService`へ接続する登録層 |
| `cogs/playback_cog.py` | `/clear`と`on_message`を読み上げサービスへ接続する登録層 |
| `cogs/general_cog.py` | `/version`を登録する一般コマンド層 |
| `cogs/lifecycle_cog.py` | 起動・サーバー参加退出・Soundboard更新イベントを登録するライフサイクル層 |
| `cogs/user_cog.py` | DMを含むユーザー単位設定コマンドを登録する層 |
| `clients/voicevox_client.py` | 再利用可能な非同期HTTPセッションでVOICEVOX生成とWAV保存を担当 |
| `clients/discord_soundboard_client.py` | Discord Soundboard一覧取得・再生APIを担当する非同期HTTPクライアント |
| `clients/managed_discord_client.py` | Bot終了時に外部HTTP・SQLiteリソースを閉じるDiscord Client |
| `swap.py` | SQLiteを知らない純粋なテキスト前処理エンジン。`DictionarySnapshot`を入力に辞書・URL・絵文字・メンション・Markdownを処理 |
| `word_dict.py` | `DictionaryRepository`を利用する辞書サービス窓口（`DictManager`）と `/dict` コマンド群 |
| `sound_dict.py` | サウンドボード辞書と `/sounddict` コマンド群。Clientから受け取った一覧を`SoundboardCacheRepository`へ渡す |
| `repositories/guild_config_repository.py` | `config.db`のSQLite操作、設定値バリデーション、VOICEVOX値変換 |
| `repositories/dictionary_repository.py` | `dict.db`のSQLite操作と前処理用`DictionarySnapshot`の生成 |
| `repositories/soundboard_cache_repository.py` | `soundboards.db`のSQLite操作とサウンド一覧同期 |
| `repositories/user_config_repository.py` | `users.db`のユーザー読みと、準備領域であるユーザー別個人辞書を管理するRepository |
| `setting.py` | `/setting` コマンド群（サーバー管理者向け設定変更） |
| `presentation/embeds.py` | Discord Embedの共通ひな形（色・タイトル・本文） |
| `presentation/error_handler.py` | コマンド実行時の共通エラー応答 |
| `models/audio_item.py` | 再生キュー要素（`TTSItem` / `SoundboardItem`）の型定義 |
| `models/guild_session.py` | ギルド単位のキュー・タスク・一時チャンネル・スキップ状態を保持する `GuildSession` |
| `models/dictionary_snapshot.py` | Repositoryから純粋な前処理へ渡す読み辞書・Soundboard条件のスナップショット |
| `models/preprocess_result.py` | 前処理済みテキスト・置換範囲・Soundboard ID・空白区切り文章フラグを保持する結果型 |
| `config.py` | `.env` から環境変数をロードし定数として公開 |
| `backup.py` | SQLiteの定時バックアップとローテーション管理 |
| `migration.py` | 旧worddict.db / sounddict.db → 統合 dict.db へのマイグレーションツール |
| `migration2v3.py` | v2 JSON形式 → v3 SQLite形式へのマイグレーションツール |
| `dict_view.py` | 辞書表示用ページネーションUI（`DictViewPaginator`、20件/ページ） |

---

## データベーススキーマ

### config.db — `guild_config`

```sql
CREATE TABLE guild_config (
    guild_id      TEXT    PRIMARY KEY,
    TextTarget    INTEGER,                      -- 読み上げテキストチャンネルID（NULL=未設定）
    VoiceTarget   INTEGER,                      -- AutoJoin対象VCチャンネルID（NULL=未設定）
    Speaker       INTEGER,                      -- VOICEVOX話者ID（NULL=DEFAULT_SPEAKER使用）
    Volume        INTEGER NOT NULL DEFAULT 100, -- 音量 0〜100
    Speed         INTEGER NOT NULL DEFAULT 100, -- 速度 50〜200
    SpacedSpeed   INTEGER DEFAULT NULL,         -- 空白区切り文章の速度 50〜200（NULL=デフォルト75）
    MaxChar       INTEGER NOT NULL DEFAULT 50,  -- 最大文字数 30〜200
    AutoJoin      INTEGER NOT NULL DEFAULT 0,   -- 自動入室 0/1
    AccessNotice  INTEGER NOT NULL DEFAULT 1,   -- 入退室通知 0/1
    Language      TEXT    NOT NULL DEFAULT 'ja',-- v3.4以前とのDB互換性のため残置（v3.5では未使用）
    Greeting      INTEGER NOT NULL DEFAULT 1    -- 起動挨拶 0/1
)
```

`Speaker` が NULL のとき `_to_python()` は環境変数 `DEFAULT_SPEAKER`（デフォルト8）を返す。  
`SpacedSpeed` が NULL のとき、空白区切り文章はデフォルト速度75を使用する。`/setting spaced-speed-reset`も値をNULLへ戻すため、実効速度は75になる。`AutoJoin`/`AccessNotice`/`Greeting` はSQLite上は 0/1、Python上は bool で扱う（`_BOOL_KEYS` で変換）。
新しいサーバーの行は`AccessNotice=1`を明示して作り、入退室通知を有効にする。v4.2.0.70より前に作ったDBは表の既定値が0のままだが、値を明示するので新しいサーバーでは有効になる。すでに行があるサーバーの値は変えない。
既存DBに`SpacedSpeed`を追加する直前に、`BACKUP_DIR`へ`backup_config_YYYYMMDD_HHMMSS_v3-latest.db`を作成する。バックアップ失敗時はスキーマ変更せず起動を中止し、この移行前バックアップは定時バックアップのローテーション対象外とする。
`Language` は既存DBとの互換性のためカラムのみ維持し、v3.5の設定API・コマンドからは参照しない。Botの表示言語は日本語固定。

### dict.db — `dict`

```sql
CREATE TABLE dict (
    guild_id        TEXT    NOT NULL,
    word            TEXT    NOT NULL,
    reading         TEXT,              -- 読み仮名（NULL可）
    sound_id        TEXT,              -- Discordサウンドボード sound_id（NULL可）
    is_priority     INTEGER NOT NULL DEFAULT 0, -- 0=通常、1=優先（URL処理より先に適用）
    full_match      INTEGER NOT NULL DEFAULT 1, -- 1=完全一致、0=部分一致（sound_id専用）
    trigger_user_id TEXT    DEFAULT NULL,        -- このユーザーIDが発言時のみ再生（NULL=全員）
    added_at        INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (guild_id, word)
)
CREATE INDEX idx_dict_guild ON dict (guild_id)
```

- `guild_id = '__common__'` で全サーバー共通の辞書エントリ
- `reading` と `sound_id` の両方を持てる（メッセージ全文一致時は sound_id 優先）
- `is_priority = 1` はURL処理より前に適用される
- `is_priority`は前処理順を制御する内部区分であり、`/dict view`・`/sounddict view`では区別せず登録・更新日時の降順で表示する
- `full_match` / `trigger_user_id` は `sound_id` を持つエントリのみ有効
- `full_match = 0` のとき、`word` がメッセージ中に含まれれば再生（部分一致）
- `trigger_user_id` が非NULLのとき、そのユーザー/BotのメッセージID一致時のみ再生
- カラムは起動時に `ALTER TABLE ... ADD COLUMN` で自動追加（既存DBに対するマイグレーション済み）

### soundboards.db — `soundboards`

```sql
CREATE TABLE soundboards (
    guild_id  TEXT NOT NULL,
    sound_id  TEXT NOT NULL,
    name      TEXT NOT NULL,
    PRIMARY KEY (guild_id, sound_id)
)
```

`/sounddict add` のオートコンプリート用キャッシュ。Discordイベント（`GUILD_SOUNDBOARD_SOUND_*`）で同期。

### users.db — `user_settings` / `user_dictionary`

```sql
CREATE TABLE user_settings (
    user_id TEXT PRIMARY KEY,
    reading TEXT DEFAULT NULL
)

CREATE TABLE user_dictionary (
    user_id  TEXT NOT NULL,
    word     TEXT NOT NULL,
    reading  TEXT NOT NULL,
    added_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (user_id, word),
    FOREIGN KEY (user_id) REFERENCES user_settings(user_id) ON DELETE CASCADE
)
```

ユーザー設定はサーバー横断で扱う。`reading`は`/user-reading`で本人が設定し、メンションと入退室通知の読み上げ時にDiscord表示名より優先する。名前解決は`UserReadingService.get_reading()`へ集約し、未設定時はDiscord表示名を返す。`user_dictionary`はそのユーザーが発言した場合だけ適用する個人辞書の準備領域であり、現時点では読み上げパイプラインやコマンドへ接続しない。

---

## テキスト前処理パイプライン（`swap.py` `preprocess_text`）

処理順は以下の通り（**順番変更はバグの原因になるため注意**）:

0. 空白区切り文字列を正規化。同一文字反復と異なる3文字以上の部分列は文中でも連結し、メッセージ全文が異なる3文字以上の空白区切りなら `spaced_out=True` にする。通常文との混在時はフラグを立てない。前後の語の端にくっついた1文字ずつの区切り（例: 「とてもよ　く　な　い天気」）は、区切られた1文字が2つ以上あり、前後の語の端の文字と合わせて異なる3文字以上なら連結する。英語の文を詰めないよう、この連結はASCII以外の文字だけを対象にする
1a. 正規化後のメッセージ全文が `sound_id` 付き辞書エントリと**完全一致**（`full_match=1`）かつ `trigger_user_id` 条件を満たす → サウンドボード再生してスキップ
1b. `sound_id` 付き辞書エントリのうち `full_match=0` のもので正規化後のメッセージ中に**部分一致**し `trigger_user_id` 条件を満たす → サウンドボード再生してスキップ（1a より後に評価）
2. 優先辞書（`is_priority=1`）を適用
3. URLパターンをリンク説明文に変換（YouTube→ユーチューブへのリンク、等）
4. **カスタム絵文字**（`<:name:id>` / `<a:name:id>`）をフィルタ ← **wwwより必ず前に実行**
5. `ww`/`www` パターン → `わら` × 文字数に変換
6. スポイラー・取り消し線・コードブロック・タイムスタンプを変換
7. ユーザーメンション → ユーザー設定の読み方（未設定時は表示名）+ "へのメンション"
8. チャンネルリンク → チャンネル名 + "へのリンク"
9. ロールメンション → ロール名 + "へのメンション"
10. 改行・スペースを区切りに変換
11. 通常辞書（`is_priority=0`）→ 共通辞書（`__common__`）を適用
12. 絵文字短縮名（`emoji_ja.json`）を適用
13. 添付ファイル種別の説明を追加

`_apply_regex` / `_apply_dict` は `(text, protected: bool)` のセグメントリストで動作。`protected=True` のセグメントは以降の処理でスキップされる。

`swap.preprocess_text(text, dictionary, emoji_ja, guild, attachments, mentions, author_id=None, user_readings=None)` は `PreprocessResult(text, replaced_ranges, sound_id, spaced_out)` を返す。`dictionary` は`DictionaryRepository`が生成した`DictionarySnapshot`、`user_readings`は`SpeechService`が`UserReadingService`で解決したユーザーIDと読み方のマッピング。`swap.py`はSQLite接続を受け取らない。

`DictManager.preprocess_text(text, guild_id, guild, attachments, mentions, author_id=None, user_readings=None)` は辞書サービスの窓口であり、内部でRepositoryからスナップショットを取得して`swap.preprocess_text`を呼ぶ。最大文字数チェック・トリミングは前処理後に`SpeechService`が行う。

---

## 辞書のエクスポート・インポート（`/dict export`・`/dict import`）

形式はShovelと同じで、UTF-8のテキストに1行に1件、`単語, 読み`と書く。書き出すファイルの名前は`exported_<guild_id>.dict`。

- 対象はそのサーバーの読み方を持つ項目だけ。音声辞書の設定と`__common__`は扱わない。`is_priority`は保存せず、読み込むときに単語から決め直す。
- 書き出しでは、読み方にカンマを含む項目を入れない（読み込むときに単語と読み方の境目が分からなくなるため）。
- 読み込みでは、行の最後のカンマで単語と読み方に分ける。空の行と、`#`で始まりカンマのない行は読み飛ばす。
- 読み込むファイルは1MBまで、登録する単語は1回に5,000件まで。超えたとき、またはUTF-8で読めないときは、ファイル全体を読み込まない。
- すでに読み方がある単語は上書きしない。音声辞書だけがある単語には、`sound_id`を残したまま読み方を足す。
- 書き込みは`DictionaryRepository.import_readings()`が1つのトランザクションで行い、途中で失敗したら何も登録しない。
- 読み込まなかった行は`行番号: 元の行 — 理由`の一覧にして、実行者へDMで送る。DMを送れなかったときは、実行者にだけ見える形で送る。
- 権限は、両方とも当面はサーバー管理者（`manage_guild`）に限る。

---

## チャンネル判定ロジック（`services/message_service.py`）

```
temp_text_targets（一時設定）→ TextTarget（永続設定）の順で参照。
どちらも設定されていない場合: VCのテキストチャット（同一channel_id）のみ読む。
どちらかが設定されている場合: 設定チャンネル OR VCのテキストチャット のどちらかに一致すれば読む。
```

`GuildSession.temporary_text_channel_id` は `/join` 実行時にコマンドチャンネルへ設定する。`/setting text-target`を実行すると一時設定を消し、設定したチャンネルをすぐに読み上げ対象にする。VCのテキストチャットは一時設定や`TextTarget`に関係なく常に読む。`ConnectionService`はBotが強制切断された場合に`pending_text_channel_id`へ退避し、再接続時に復元する。自発的な切断は`voluntary_disconnects`で識別し、一時設定を破棄する。

**Botメッセージの扱い（2026-06-27 追加）:**  
Bot からのメッセージは通常 TTS をスキップするが、sounddict に一致する場合のみサウンドボードを再生する。チャンネル判定は人間ユーザーと同じロジック（`TextTarget` / VC テキストチャット）を適用。`SpeechService.add_message(sounddict_only=True)` で呼び出される。

---

## スラッシュコマンド

| コマンド | 引数 | 権限 |
|---|---|---|
| `/join` | `change_channel: bool` | VCにいるユーザー |
| `/leave` | — | VCにいるユーザー |
| `/version` | — | 全員 |
| `/clear` | `instant: bool` | 全員 |
| `/user-reading` | `reading: str (1〜50文字)` | 全員（DM可・ephemeral応答） |
| `/dict add` | `word`, `read` | 全員 |
| `/dict del` | `word`, `both: bool` | 全員 |
| `/dict view` | `search: str`, `ephemeral: bool` | 全員 |
| `/dict export` | `ephemeral: bool` | manage_guild |
| `/dict import` | `file: Attachment` | manage_guild |
| `/sounddict add` | `word`, `sound`, `read: str`, `full_match: bool`, `trigger_user: Member` | 全員 |
| `/sounddict del` | `word`, `both: bool` | 全員 |
| `/sounddict view` | `search: str`, `ephemeral: bool` | 全員 |
| `/setting view` | — | manage_guild |
| `/setting text-target` | `channel` | manage_guild |
| `/setting text-target-reset` | — | manage_guild |
| `/setting voice-target` | `channel` | manage_guild |
| `/setting voice-target-reset` | — | manage_guild |
| `/setting speaker` | `speaker` | manage_guild |
| `/setting volume` | `volume: int (0〜100)` | manage_guild |
| `/setting speed` | `speed: int (50〜200)` | manage_guild |
| `/setting spaced-speed` | `speed: int (50〜200)` | manage_guild |
| `/setting spaced-speed-reset` | — | manage_guild |
| `/setting max-char` | `chars: int (30〜200)` | manage_guild |
| `/setting auto-join` | `enabled: bool`（有効にするとき`VoiceTarget`が未設定なら、実行者が入っているVCを記録する。VCに入っていなければ断る） | manage_guild |
| `/setting access-notice` | `enabled: bool` | manage_guild |

メッセージトリガー:
- `@Bot` 単体メンション: VC接続トグル
- `s`: 現在再生中をスキップ
- `c`: 読み上げキューをすべてクリア（`/clear instant:True` と同じ）。クリアのたびに`GuildSession.clear_generation`を上げ、クリアの時点で音声を生成中だったメッセージも読み上げない
- `!s ` プレフィックスまたは silent フラグ: 読み上げをスキップ

---

## 外部HTTP API

- VOICEVOX通信は`VoicevoxClient`、Discord Soundboard通信は`DiscordSoundboardClient`だけが担当する。
- Discord Gateway接続前に`VoicevoxClient.check_health()`で`/version`へ疎通確認し、失敗した場合はBotの起動を中止する。
- 稼働中の音声生成は全ギルドで1件ずつ処理し、一時的なHTTP障害だけを受付から最大30秒再試行する。一時切断後はClient所有のHTTPセッションを再生成する。
- 両Clientは`aiohttp.ClientSession`を初回通信時に生成して再利用する。通常実行経路で同期`requests`は使用しない。
- `VoiceService`と`UpdateSoundBoards`はHTTPのURL・認証方法を知らず、Clientのメソッドだけを呼ぶ。
- Bot終了時は`ManagedDiscordClient.close()`がHTTPセッションと4つのSQLite接続を閉じる。

---

## 起動の準備完了の知らせ

`on_ready`の処理（コマンドの同期、ギルドの整理、Soundboardの更新、バックアップの開始、ステータスの更新）がすべて終わったあとに、`ReadyStateService.mark_ready()`が`UPDATE_STATE_DIR/ready.json`へバージョン・プロセスID・時刻を書く。一時ファイルから置き換えるので、書きかけを読まれることはない。Botの終了時には`ManagedDiscordClient.close()`の中でこのファイルを消す。書き込みに失敗してもBotは止めない。インストーラーは起動前にこのファイルを消し、サービスの今のプロセスIDと期待するバージョンが書かれるまで最大60秒待つ。

---

## インストーラー（`installer/`）

サーバーへの導入・更新・管理を行う`tsumugium-setup`の本体。標準ライブラリだけで動き、Python 3.10以上を前提にする。利用者向けの手順は[`docs/INSTALL.md`](docs/INSTALL.md)、更新の流れは[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)を参照。

| ファイル | 役割 |
|---|---|
| `install.sh` | 展開したzipから最初に実行する入口。Python 3.10以上かを確かめ、隣の`installer/`を呼ぶ |
| `installer/cli.py` | 8つのモードの振り分け。想定している失敗は理由を表示して終了コード1で終える |
| `installer/install.py` | `install`モード |
| `installer/upgrade.py` | `upgrade`・`repair`モード。取得・照合・展開までを今のバージョンが行い、そのあとを新しいバージョンに引き継ぐ |
| `installer/handoff.py` | 引き継ぎを受ける新しいバージョンの入口 |
| `installer/rollback.py` | `rollback`モード。`app.prev/`にある1つ前のバージョンのインストーラーで動く |
| `installer/manage.py` | `status`・`start`・`stop`・`uninstall`モード |
| `installer/entry.py` | `/usr/local/sbin/tsumugium-setup`の中身と、置き方・消し方 |
| `installer/release.py` | GitHub Releasesからのバージョンの選択・取得、SHA256値の照合、zipの安全な展開、バージョンの比較 |
| `installer/checks.py` | OS・systemd・Python・必要なパッケージ・VOICEVOXの事前チェック |
| `installer/envfile.py` | `.env`の確認と、更新時に足りない項目を書き足す処理 |
| `installer/service.py` | 専用ユーザー、ディレクトリの持ち主と権限、systemd unit |
| `installer/readiness.py` | `ready.json`による起動の確認 |
| `installer/deploy.py` | venvの作成と、`requirements.lock`からの依存の導入 |
| `installer/instance.py` | 名前の検証、置き場所の構成、記録（`/etc/tsumugium/<インスタンス名>.json`）、インスタンスごとのロック |
| `installer/system.py`・`installer/ui.py` | コマンドの実行とパスの組み立て、端末での表示と質問。テストでは偽物に差し替える |
| `scripts/build_release.py` | 配布用のzipと`.sha256`を作る（CIで使う） |
| `scripts/ci_installer_e2e.py` | CIの結合テスト。本物のsystemdでinstallからuninstallまでを動かす |
| `scripts/migrate_legacy.py` | 旧環境から`.env`と`data/`を作る移行ツール。配布用のzipには入れず、移行する人に直接渡す |

- 新しいバージョンへ引き継ぐ形式には番号（`installer/__init__.py`の`HANDOFF_VERSION`）を付ける。引き継ぐ項目の意味を変えるときは番号を上げる。知らない番号を受け取ったバージョンは、何も変えずに止まる。
- インストーラーを別のプロセスで動かすときは、`python -m installer`を使わず、`installer.python_command()`（`python -I`で起動する）を使う。`-m`は今いるディレクトリの`installer/`を先に読むので、展開したzipやリポジトリの中で実行すると、違うバージョンが動くため。
- インストーラーが作るunitと入口には`# Managed by tsumugium-setup`の印を付ける。印のない同じ名前のファイルは、上書きも削除もしない。
- 配布用のzipには`.claude/`・`.github/`・`docs/`・`scripts/`・`tests/`・`.gitignore`・`AGENTS.md`を入れず、`build-info.json`と`requirements.lock`を加える。
- `requirements.lock`は、CIで`uv pip compile --universal`を使ってハッシュ値つきで作る。`--universal`を付けると、Python 3.13以上だけで要る依存（`audioop-lts`など）も条件付きで書き出されるので、Python 3.10以上のどのバージョンでも同じロックを使える。インストーラーは`pip install --require-hashes`で入れる。CIの`lock-check`ジョブがPython 3.10〜3.14のそれぞれで入れられることを確かめる。
- 記録の形式を変えるときは、`installer/instance.py`の`RECORD_FORMAT`を上げる。知らない項目は読み飛ばすので、項目を足すだけなら上げなくてよい。

---

## 音声再生アーキテクチャ（`services/voice_service.py`）

`VoiceService.sessions: dict[int, GuildSession]` でギルドごとの実行状態を保持する。`GuildSession.queue` に音声アイテムを積み、`play_loop` が順次消費する。キュー投入は `VoiceService.enqueue()` に集約する。

`GuildSession` はキューのほか、再生タスク、キープアライブタスク、一時テキストチャンネル、再接続時の復元待ちチャンネル、スキップ・クリア状態を保持する。

### キューアイテム形式

| 型 | 形式 | 説明 |
|---|---|---|
| TTS | `TTSItem(path: str)` | VOICEVOX 生成 WAV ファイルパス |
| Soundboard | `SoundboardItem(sound_id: str)` | Discord サウンドボード |

`isinstance(item, TTSItem)` / `isinstance(item, SoundboardItem)` で判別する。

### `play_loop` の処理フロー

1. キューからアイテムを取り出す（300秒タイムアウト）
2. `item` が `SoundboardItem` の場合:
   - `voice_client.is_playing()` が False になるまで 0.1秒ポーリングで待機（TTS 完了待ち）
   - `_play_soundboard()` で Discord API を呼び出し、即 `task_done()`
3. `item` が `TTSItem` の場合: `play()` で FFmpeg 再生、完了後 `task_done()`

Botが接続し直したときは、`VoiceService.reset_speaking_soon()`が1秒後に無音のOpusフレームを5つ送ってから「話していない」状態を送る。話している途中で強制切断されたあと、Discord上で「話している」表示が残らないようにするため。

**注意:** サウンドボード同士は互いに待機しない（Discord API は再生完了イベントを返さないため）。サウンドボード再生中でも次のアイテムは即時処理される。

### `/clear` の動作

キュードレイン時に `TTSItem` のみ `safe_remove()` でファイル削除。`SoundboardItem` はスキップ。

---

## バージョン表記

- 正式リリースのバージョンは`vX.X.X.XX`で表記する（例: `v4.1.0.69`）。先頭3つの`X.X.X`は[セマンティックバージョニング](https://semver.org/lang/ja/)に従う。末尾の`XX`は累積リリース回数であり、リリースのたびに1ずつ増やし、`X.X.X`が上がってもリセットしない。
- ベータ版は`vX.X.X.XX (beta X)`で表記する。
  - 前半の`vX.X.X.XX`には、次回リリースで使う予定のバージョンを書く。
  - 後半の`(beta X)`には、前回リリース以降のコミットのうち、実際にコードを書き換えたコミットの数を1から累積で書く。ドキュメントだけを変更したコミットなど、コードを書き換えないコミットは数えない。
  - ベータ番号はリリースごとにリセットし、次のリリースに向けた最初のコード変更を`(beta 1)`とする。
- 例: v4.1.0.69をリリースした後、次期リリースをマイナーバージョンアップとする場合、次期バージョンはv4.2.0.70となる。このときコードを書き換えた1回目のコミットは`v4.2.0.70 (beta 1)`、3回目は`v4.2.0.70 (beta 3)`となる。v4.2.0.70のリリース後は、次期バージョンで再び`(beta 1)`から数える。

---

## 開発用テストの運用

- `tests/` はリポジトリに含めて公開する。
- テストには実在するDiscordのID・トークン・サーバー名・ユーザー名と、実際の辞書の登録内容を使わない。IDは `111111111111111111` のような明らかなダミーにする。
- DBはテストごとに一時ディレクトリへ作り、本番DBのコピーは使わない。
- テストはネットワークに接続しない。VOICEVOX・Discord・GitHubとのHTTP通信は、テスト内で立てるローカルのサーバーかモックで確かめる。
- テストには標準ライブラリの `unittest` だけを使い、依存を増やさない。
- 全テストはプロジェクトルートで `python -m unittest discover -s tests -v` を実行する。
- 特定モジュールだけ実行する場合は、例として `python -m unittest tests.test_http_clients -v` を使用する。
- リリース・コミット前にも必要な回帰テストを実行する。
- インストーラーの結合テストは、CIの`installer-e2e`ジョブが`scripts/ci_installer_e2e.py`で行う。本物の`sudo`・`useradd`・systemdを使い、`/opt/tsumugium`などを作って消すので、手元のマシンでは実行しない。

---

## 環境変数（`.env`）

インストーラーで入れた場合、`SERVER_CONFIG_DB`・`DICT_DB`・`SOUND_BOARDS_DB`・`USER_CONFIG_DB`・`TMP_DIR`・`BACKUP_DIR`・`VOICEVOX_URL`・`UPDATE_STATE_DIR`・`TSUMUGIUM_ENV_FILE`はsystemd unitの`Environment=`で渡すので、`.env`に書いても使われない。`.env.template`では、これらを手で動かすとき用の`#`付きの例として載せ、インストーラーが更新のときに`.env`へ書き足さないようにする。行がない項目は既定値を使うが、行があって空欄の項目は空文字として扱う。

| 変数 | デフォルト | 説明 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | 必須 | Discordボットトークン |
| `OPERATOR_USER_ID` | `""` | エラー通知DMを受け取る運営者のDiscordユーザーID（未設定・空欄で無効） |
| `VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンドポイント |
| `DEFAULT_SPEAKER` | `8` | デフォルト話者ID（Speaker=NULL時に使用） |
| `SERVER_CONFIG_DB` | `db/config.db` | サーバー設定DB |
| `DICT_DB` | `db/dict.db` | 辞書DB |
| `SOUND_BOARDS_DB` | `db/soundboards.db` | サウンドボードキャッシュDB |
| `USER_CONFIG_DB` | `db/users.db` | ユーザー読み・ユーザー別個人辞書DB |
| `EMOJI_JA_JSON` | `db/emoji_ja.json` | 絵文字→日本語短縮名マッピング |
| `SPEAKERS_JSON` | `db/speakers.json` | VOICEVOX話者リスト |
| `TMP_DIR` | `tmp` | 音声ファイル一時保存先 |
| `BACKUP_DIR` | `backup` | バックアップ保存先 |
| `BACKUP_TIMES` | `12:00` | バックアップ実行時刻（カンマ区切り、例: `03:00,15:00`）。空欄にするとバックアップを取らない |
| `BACKUP_INTERVAL_DAYS` | `1` | バックアップ実行間隔（日） |
| `BACKUP_KEEP` | `7` | バックアップ保持世代数 |
| `STATUS_MESSAGE` | `""` | Botのステータスメッセージ。`{voice_connections}`（接続VC数）、`{voice_users}`（同席中の人間ユーザー数）、`{guilds}`（参加サーバー数）を利用可能 |
| `UPDATE_STATE_DIR` | `""` | `ready.json`の置き場所。インストーラーがsystemd unitで渡す。空欄なら書かない |
| `TSUMUGIUM_ENV_FILE` | `""` | 読み込む`.env`の場所。インストーラーがsystemd unitで渡す。空欄なら、今までどおり`.env`を探して読む。どちらの場合も、すでにある環境変数は上書きしない |
