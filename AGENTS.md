# Tsumugium — Discord 読み上げBot 仕様書

**バージョン**: 3.6.0.68 / **最終更新**: 2026-08-31
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
| `cogs/connection_cog.py` | `/join`・`/leave`とVC状態イベントを`ConnectionService`へ接続する登録層 |
| `cogs/playback_cog.py` | `/clear`と`on_message`を読み上げサービスへ接続する登録層 |
| `cogs/general_cog.py` | `/version`を登録する一般コマンド層 |
| `cogs/lifecycle_cog.py` | 起動・サーバー参加退出・Soundboard更新イベントを登録するライフサイクル層 |
| `clients/voicevox_client.py` | 再利用可能な非同期HTTPセッションでVOICEVOX生成とWAV保存を担当 |
| `clients/discord_soundboard_client.py` | Discord Soundboard一覧取得・再生APIを担当する非同期HTTPクライアント |
| `clients/managed_discord_client.py` | Bot終了時に外部HTTP・SQLiteリソースを閉じるDiscord Client |
| `swap.py` | SQLiteを知らない純粋なテキスト前処理エンジン。`DictionarySnapshot`を入力に辞書・URL・絵文字・メンション・Markdownを処理 |
| `word_dict.py` | `DictionaryRepository`を利用する辞書サービス窓口（`DictManager`）と `/dict` コマンド群 |
| `sound_dict.py` | サウンドボード辞書と `/sounddict` コマンド群。Clientから受け取った一覧を`SoundboardCacheRepository`へ渡す |
| `repositories/guild_config_repository.py` | `config.db`のSQLite操作、設定値バリデーション、VOICEVOX値変換 |
| `repositories/dictionary_repository.py` | `dict.db`のSQLite操作と前処理用`DictionarySnapshot`の生成 |
| `repositories/soundboard_cache_repository.py` | `soundboards.db`のSQLite操作とサウンド一覧同期 |
| `setting.py` | `/setting` コマンド群（サーバー管理者向け設定変更） |
| `presentation/embeds.py` | Discord Embedの共通ひな形（色・タイトル・本文） |
| `presentation/error_handler.py` | コマンド実行時の共通エラー応答 |
| `models/audio_item.py` | 再生キュー要素（`TTSItem` / `SoundboardItem`）の型定義 |
| `models/guild_session.py` | ギルド単位のキュー・タスク・一時チャンネル・スキップ状態を保持する `GuildSession` |
| `models/dictionary_snapshot.py` | Repositoryから純粋な前処理へ渡す読み辞書・Soundboard条件のスナップショット |
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
    MaxChar       INTEGER NOT NULL DEFAULT 50,  -- 最大文字数 30〜200
    AutoJoin      INTEGER NOT NULL DEFAULT 0,   -- 自動入室 0/1
    AccessNotice  INTEGER NOT NULL DEFAULT 0,   -- 入退室通知 0/1
    Language      TEXT    NOT NULL DEFAULT 'ja',-- v3.4以前とのDB互換性のため残置（v3.5では未使用）
    Greeting      INTEGER NOT NULL DEFAULT 1    -- 起動挨拶 0/1
)
```

`Speaker` が NULL のとき `_to_python()` は環境変数 `DEFAULT_SPEAKER`（デフォルト8）を返す。  
`AutoJoin`/`AccessNotice`/`Greeting` はSQLite上は 0/1、Python上は bool で扱う（`_BOOL_KEYS` で変換）。
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

---

## テキスト前処理パイプライン（`swap.py` `preprocess_text`）

処理順は以下の通り（**順番変更はバグの原因になるため注意**）:

1a. メッセージ全文が `sound_id` 付き辞書エントリと**完全一致**（`full_match=1`）かつ `trigger_user_id` 条件を満たす → サウンドボード再生してスキップ
1b. `sound_id` 付き辞書エントリのうち `full_match=0` のものでメッセージ中に**部分一致**し `trigger_user_id` 条件を満たす → サウンドボード再生してスキップ（1a より後に評価）
2. 優先辞書（`is_priority=1`）を適用
3. URLパターンをリンク説明文に変換（YouTube→ユーチューブへのリンク、等）
4. **カスタム絵文字**（`<:name:id>` / `<a:name:id>`）をフィルタ ← **wwwより必ず前に実行**
5. `ww`/`www` パターン → `わら` × 文字数に変換
6. スポイラー・取り消し線・コードブロック・タイムスタンプを変換
7. ユーザーメンション → 表示名 + "へのメンション"
8. チャンネルリンク → チャンネル名 + "へのリンク"
9. ロールメンション → ロール名 + "へのメンション"
10. 改行・スペースを区切りに変換
11. 通常辞書（`is_priority=0`）→ 共通辞書（`__common__`）を適用
12. 絵文字短縮名（`emoji_ja.json`）を適用
13. 添付ファイル種別の説明を追加

`_apply_regex` / `_apply_dict` は `(text, protected: bool)` のセグメントリストで動作。`protected=True` のセグメントは以降の処理でスキップされる。

`swap.preprocess_text(text, dictionary, emoji_ja, guild, attachments, mentions, author_id=None)` は `(text, replaced_ranges, sound_id)` を返す。`dictionary` は`DictionaryRepository`が生成した`DictionarySnapshot`。`swap.py`はSQLite接続を受け取らない。

`DictManager.preprocess_text(text, guild_id, guild, attachments, mentions, author_id=None)` は辞書サービスの窓口であり、内部でRepositoryからスナップショットを取得して`swap.preprocess_text`を呼ぶ。最大文字数チェック・トリミングは前処理後に`SpeechService`が行う。

---

## チャンネル判定ロジック（`services/message_service.py`）

```
temp_text_targets（一時設定）→ TextTarget（永続設定）の順で参照。
どちらも設定されていない場合: VCのテキストチャット（同一channel_id）のみ読む。
どちらかが設定されている場合: 設定チャンネル OR VCのテキストチャット のどちらかに一致すれば読む。
```

`GuildSession.temporary_text_channel_id` は `/join` 実行時にコマンドチャンネルへ設定する。`ConnectionService`はBotが強制切断された場合に`pending_text_channel_id`へ退避し、再接続時に復元する。自発的な切断は`voluntary_disconnects`で識別し、一時設定を破棄する。

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
| `/dict add` | `word`, `read` | 全員 |
| `/dict del` | `word`, `both: bool` | 全員 |
| `/dict view` | `search: str`, `ephemeral: bool` | 全員 |
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
| `/setting max-char` | `chars: int (30〜200)` | manage_guild |
| `/setting auto-join` | `enabled: bool` | manage_guild |
| `/setting access-notice` | `enabled: bool` | manage_guild |

メッセージトリガー:
- `@Bot` 単体メンション: VC接続トグル
- `s`: 現在再生中をスキップ
- `!s ` プレフィックスまたは silent フラグ: 読み上げをスキップ

---

## 外部HTTP API

- VOICEVOX通信は`VoicevoxClient`、Discord Soundboard通信は`DiscordSoundboardClient`だけが担当する。
- Discord Gateway接続前に`VoicevoxClient.check_health()`で`/version`へ疎通確認し、失敗した場合はBotの起動を中止する。
- 両Clientは`aiohttp.ClientSession`を初回通信時に生成して再利用する。通常実行経路で同期`requests`は使用しない。
- `VoiceService`と`UpdateSoundBoards`はHTTPのURL・認証方法を知らず、Clientのメソッドだけを呼ぶ。
- Bot終了時は`ManagedDiscordClient.close()`がHTTPセッションと3つのSQLite接続を閉じる。

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

**注意:** サウンドボード同士は互いに待機しない（Discord API は再生完了イベントを返さないため）。サウンドボード再生中でも次のアイテムは即時処理される。

### `/clear` の動作

キュードレイン時に `TTSItem` のみ `safe_remove()` でファイル削除。`SoundboardItem` はスキップ。

---

## 開発用テストの運用

- `tests/` はBotの本番実行には不要であり、公開OSSリポジトリには含めない。`.gitignore`で管理対象外とする。
- テストコードは開発者へ別ルートで配布する。受け取った開発者はプロジェクト直下へ `tests/` を配置する。
- 全テストはプロジェクトルートで `python -m unittest discover -s tests -v` を実行する。
- 特定モジュールだけ実行する場合は、例として `python -m unittest tests.test_http_clients -v` を使用する。
- `tests/` がGit管理対象外でもローカルファイルは削除されない。リリース・コミット前にも必要な回帰テストを実行する。

---

## 環境変数（`.env`）

| 変数 | デフォルト | 説明 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | 必須 | Discordボットトークン |
| `OPERATOR_USER_ID` | `""` | エラー通知DMを受け取る運営者のDiscordユーザーID（未設定・空欄で無効） |
| `VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンドポイント |
| `DEFAULT_SPEAKER` | `8` | デフォルト話者ID（Speaker=NULL時に使用） |
| `SERVER_CONFIG_DB` | `db/config.db` | サーバー設定DB |
| `DICT_DB` | `db/dict.db` | 辞書DB |
| `SOUND_BOARDS_DB` | `db/soundboards.db` | サウンドボードキャッシュDB |
| `EMOJI_JA_JSON` | `db/emoji_ja.json` | 絵文字→日本語短縮名マッピング |
| `SPEAKERS_JSON` | `db/speakers.json` | VOICEVOX話者リスト |
| `TMP_DIR` | `tmp` | 音声ファイル一時保存先 |
| `BACKUP_DIR` | `backup` | バックアップ保存先 |
| `BACKUP_TIMES` | `""` | バックアップ実行時刻（カンマ区切り、例: `03:00,15:00`） |
| `BACKUP_INTERVAL_DAYS` | `1` | バックアップ実行間隔（日） |
| `BACKUP_KEEP` | `7` | バックアップ保持世代数 |
| `STATUS_MESSAGE` | `""` | Botのステータスメッセージ。`{voice_connections}`（接続VC数）、`{voice_users}`（同席中の人間ユーザー数）、`{guilds}`（参加サーバー数）を利用可能 |
