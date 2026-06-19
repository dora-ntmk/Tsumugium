# Tsumugium — Discord 読み上げBot 仕様書

**バージョン**: 3.0.8 / **最終更新**: 2026-04-05  
VOICEVOXを使ったDiscordテキスト読み上げBot。

---

## モジュール構成

| ファイル | 役割 |
|---|---|
| `main.py` | エントリーポイント。`/join`, `/leave`, `/version`、VC入退室イベント（AutoJoin / AccessNotice）、サーバー参加・退出処理 |
| `play.py` | `Play` クラス。メッセージ→TTSキュー管理、音声生成・再生・スキップ、キープアライブ。`on_message` イベントも担当 |
| `swap.py` | テキスト前処理エンジン（`preprocess_text`）。辞書置換・URL/絵文字/メンション/Markdown変換などのパイプライン |
| `word_dict.py` | テキスト辞書（word→reading）のSQLite管理と `/dict` コマンド群 |
| `sound_dict.py` | サウンドボード辞書（word→sound_id）の管理と `/sounddict` コマンド群。Discordサウンドボードキャッシュ更新も担当 |
| `server_config.py` | サーバー設定のSQLite管理（`ServerConfig` クラス）。バリデーション・デフォルト値・VOICEVOX変換 |
| `setting.py` | `/setting` コマンド群（サーバー管理者向け設定変更） |
| `messages.py` | 多言語メッセージ管理。JSONファイルからロードしてEmbedを生成、コマンド翻訳（`BotTranslator`） |
| `vvtts.py` | VOICEVOX API連携。テキストからWAVファイルを生成（`VvTTS` クラス） |
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
    Language      TEXT    NOT NULL DEFAULT 'ja',-- ja / en / zh-CN / zh-TW / ko / hg
    Greeting      INTEGER NOT NULL DEFAULT 1    -- 起動挨拶 0/1
)
```

`Speaker` が NULL のとき `_to_python()` は環境変数 `DEFAULT_SPEAKER`（デフォルト8）を返す。  
`AutoJoin`/`AccessNotice`/`Greeting` はSQLite上は 0/1、Python上は bool で扱う（`_BOOL_KEYS` で変換）。

### dict.db — `dict`

```sql
CREATE TABLE dict (
    guild_id    TEXT    NOT NULL,
    word        TEXT    NOT NULL,
    reading     TEXT,              -- 読み仮名（NULL可）
    sound_id    TEXT,              -- Discordサウンドボード sound_id（NULL可）
    is_priority INTEGER DEFAULT 0, -- 0=通常、1=優先（URL処理より先に適用）
    added_at    INTEGER DEFAULT (strftime('%s', 'now')),
    PRIMARY KEY (guild_id, word)
)
CREATE INDEX idx_dict_guild ON dict (guild_id)
```

- `guild_id = '__common__'` で全サーバー共通の辞書エントリ
- `reading` と `sound_id` の両方を持てる（メッセージ全文一致時は sound_id 優先）
- `is_priority = 1` はURL処理より前に適用される

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

1. メッセージ全文が `sound_id` 付き辞書エントリと完全一致 → サウンドボード再生してスキップ
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
13. 最大文字数チェック・トリミング（`MaxChar`）

`_apply_regex` / `_apply_dict` は `(text, protected: bool)` のセグメントリストで動作。`protected=True` のセグメントは以降の処理でスキップされる。

---

## チャンネル判定ロジック（`play.py` `on_message`）

```
temp_text_targets（一時設定）→ TextTarget（永続設定）の順で参照。
どちらも設定されていない場合: VCのテキストチャット（同一channel_id）のみ読む。
どちらかが設定されている場合: 設定チャンネル OR VCのテキストチャット のどちらかに一致すれば読む。
```

`temp_text_targets` は `/join` 実行時にコマンドチャンネルへ設定。Botが強制切断された場合は `pending_temp_targets` に退避し、再接続時に復元する。

---

## スラッシュコマンド

| コマンド | 引数 | 権限 |
|---|---|---|
| `/join` | `change_channel: bool` | VCにいるユーザー |
| `/leave` | — | VCにいるユーザー |
| `/version` | — | 全員 |
| `/clear` | `instant: bool` | 全員 |
| `/dict add` | `word`, `read` | manage_guild |
| `/dict del` | `word`, `both: bool` | manage_guild |
| `/dict view` | `search: str`, `ephemeral: bool` | 全員 |
| `/sounddict add` | `word`, `sound`, `read: str` | manage_guild |
| `/sounddict del` | `word`, `both: bool` | manage_guild |
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
| `/setting language` | `language` | manage_guild |

メッセージトリガー:
- `@Bot` 単体メンション: VC接続トグル
- `s`: 現在再生中をスキップ
- `!s ` プレフィックスまたは silent フラグ: 読み上げをスキップ

---

## 環境変数（`.env`）

| 変数 | デフォルト | 説明 |
|---|---|---|
| `DISCORD_BOT_TOKEN` | 必須 | Discordボットトークン |
| `VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンドポイント |
| `DEFAULT_SPEAKER` | `8` | デフォルト話者ID（Speaker=NULL時に使用） |
| `SERVER_CONFIG_DB` | `db/config.db` | サーバー設定DB |
| `DICT_DB` | `db/dict.db` | 辞書DB |
| `SOUND_BOARDS_DB` | `db/soundboards.db` | サウンドボードキャッシュDB |
| `EMOJI_JA_JSON` | `db/emoji_ja.json` | 絵文字→日本語短縮名マッピング |
| `SPEAKERS_JSON` | `db/speakers.json` | VOICEVOX話者リスト |
| `TMP_DIR` | `tmp` | 音声ファイル一時保存先 |
| `MESSAGES_DIR` | `messages` | メッセージJSONディレクトリ |
| `BACKUP_DIR` | `backup` | バックアップ保存先 |
| `BACKUP_TIMES` | `""` | バックアップ実行時刻（カンマ区切り、例: `03:00,15:00`） |
| `BACKUP_INTERVAL_DAYS` | `1` | バックアップ実行間隔（日） |
| `BACKUP_KEEP` | `7` | バックアップ保持世代数 |
| `STATUS_MESSAGE` | `""` | Botのステータスメッセージ |
