# Tsumugium — Discord 読み上げBot 仕様書

VOICEVOXを使ったDiscordテキスト読み上げBot。

全体の依存関係と処理フローは[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)を参照。

**このプロジェクトに対する応答は基本的に日本語で行うこと。**

---

## モジュール構成

| ファイル | 役割 |
|---|---|
| `main.py` | エントリーポイント。Client・Repository・Service・Cogの生成と依存注入、Bot起動のみを担当 |
| `services/message_service.py` | メッセージの対象チャンネル判定、Bot投稿、特殊トリガーを担当。接続操作は`ConnectionService`へ委譲 |
| `services/connection_service.py` | `/join`・`/leave`・単体メンション接続、AutoJoin、AccessNotice、強制切断後の状態復元を担当。接続したときのメッセージ（Components v2）に運営者からのお知らせを載せる |
| `services/announcement_service.py` | 運営者からのお知らせの入力の確認（文字数・リンク・画像・期間）、日時の指定の読み取り、接続メッセージに載せるお知らせの選び出しを担当 |
| `services/speech_service.py` | テキスト前処理、Soundboard/TTS選択、最大文字数処理、VOICEVOX生成依頼を担当 |
| `services/voice_service.py` | ギルド別キュー、再生、スキップ、クリア、キープアライブを担当。外部API通信はClientへ委譲 |
| `services/error_notification_service.py` | エラーのCLI出力と、任意設定された運営者（複数人可）へのDiscord Embed形式DM通知を担当 |
| `services/status_service.py` | STATUS_MESSAGEの検証・変数展開と、接続状態に応じたDiscordステータス更新を担当 |
| `services/user_reading_service.py` | ユーザー設定の読み方を優先し、未設定時はDiscord表示名へ戻す共通名前解決を担当 |
| `services/dictionary_transfer_service.py` | 読み辞書を.dictファイル（CSV形式。1行に1件の`単語, 読み`）へ書き出し、読み込む純粋な処理。SQLiteとDiscordは扱わない |
| `services/update_watch_service.py` | インストーラーが`UPDATE_STATE_DIR`に置くファイルを見張る。更新中はステータスを「アップデート中」にし、更新の結果と、自動更新が人の手を要る状態を運営者へDMで知らせる |
| `services/reconnect_service.py` | つないでいるVCを`UPDATE_STATE_DIR`に書き残し、止めるときは読み上げチャンネルへ知らせてVCを抜け、再起動・更新のあとに同じVCへつなぎ直す |
| `services/ready_state_service.py` | 起動の準備が終わったことを、インストーラーへ`ready.json`で知らせる。`UPDATE_STATE_DIR`が空欄なら何もしない |
| `services/generation_log_service.py` | VOICEVOXの音声生成の記録（成功・スキップ・失敗、文字数、順番待ちと生成にかかった時間）を直近500件までメモリに持ち、集計する |
| `cogs/connection_cog.py` | `/join`・`/leave`とVC状態イベントを`ConnectionService`へ接続する登録層 |
| `cogs/playback_cog.py` | `/clear`と`on_message`を読み上げサービスへ接続する登録層 |
| `cogs/general_cog.py` | `/version`を登録する一般コマンド層 |
| `cogs/lifecycle_cog.py` | 起動・サーバー参加退出・Soundboard更新イベントを登録するライフサイクル層 |
| `cogs/user_cog.py` | DMを含むユーザー単位設定コマンドを登録する層 |
| `cogs/operator_cog.py` | Botの運営者（`OPERATOR_USER_ID`）だけが使うコマンドを登録する層。`/synthesis-log` |
| `cogs/announcement_cog.py` | 運営者だけが使う`/announce`コマンド群と、お知らせを入力するModal・入力し直すボタン・保存する前の確認（送信する・内容を変更する・破棄する）を登録する層 |
| `clients/voicevox_client.py` | 再利用可能な非同期HTTPセッションでVOICEVOX生成とWAV保存を担当 |
| `clients/discord_soundboard_client.py` | Discord Soundboard一覧取得・再生APIを担当する非同期HTTPクライアント |
| `clients/managed_discord_client.py` | Bot終了時に、登録した終了前の処理（VCから抜けるなど）を動かしてから、外部HTTP・SQLiteリソースを閉じるDiscord Client。SIGTERM・SIGINTを受けたらDiscordとの接続を閉じてから終わる |
| `swap.py` | SQLiteを知らない純粋なテキスト前処理エンジン。`DictionarySnapshot`を入力に辞書・URL・絵文字・メンション・Markdownを処理 |
| `word_dict.py` | `DictionaryRepository`を利用する辞書サービス窓口（`DictManager`）と `/dict` コマンド群 |
| `sound_dict.py` | サウンドボード辞書と `/sounddict` コマンド群。Clientから受け取った一覧を`SoundboardCacheRepository`へ渡す |
| `repositories/guild_config_repository.py` | `config.db`のSQLite操作、設定値バリデーション、VOICEVOX値変換 |
| `repositories/dictionary_repository.py` | `dict.db`のSQLite操作と前処理用`DictionarySnapshot`の生成 |
| `repositories/soundboard_cache_repository.py` | `soundboards.db`のSQLite操作とサウンド一覧同期 |
| `repositories/announcement_repository.py` | `config.db`の`announcements`表（運営者からのお知らせ）のSQLite操作 |
| `repositories/user_config_repository.py` | `users.db`のユーザー読みと、準備領域であるユーザー別個人辞書を管理するRepository |
| `setting.py` | `/setting` コマンド群（サーバー管理者向け設定変更） |
| `presentation/embeds.py` | Discord Embedの共通ひな形（色・タイトル・本文）。色は`embed_color()`でComponents v2の枠と共有する |
| `presentation/layouts.py` | Components v2（`LayoutView`）のメッセージの共通ひな形。`make_embed`と同じく、色の付いた枠に見出しと本文を入れるだけで、中の部品は呼び出し側が足す |
| `presentation/announcement_layout.py` | 運営者からのお知らせ（区切り線・各お知らせの文・リンクボタン・画像）を、`make_layout`で作った枠に足す |
| `presentation/error_handler.py` | コマンド実行時の共通エラー応答 |
| `models/audio_item.py` | 再生キュー要素（`TTSItem` / `SoundboardItem`）の型定義 |
| `models/guild_session.py` | ギルド単位のキュー・タスク・一時チャンネル・スキップ状態を保持する `GuildSession` |
| `models/dictionary_snapshot.py` | Repositoryから純粋な前処理へ渡す読み辞書・Soundboard条件のスナップショット |
| `models/streaming_audio.py` | VOICEVOXから少しずつ届く音声をWAVファイルへ書きながら、別スレッド（FFmpegへ渡す側）に書かれた分だけ読ませる`StreamingAudio` |
| `models/generation_record.py` | VOICEVOXの生成1回分の記録（`GenerationRecord`）と集計結果（`GenerationSummary`）の型 |
| `models/announcement.py` | 運営者からのお知らせ1件分（`Announcement`）の型と、ServiceとPresentationの両方で使う日時の表示（`format_time`、日本時間）・リンクの既定の表示名 |
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
    MaxChar       INTEGER NOT NULL DEFAULT 100, -- 最大文字数 30〜500
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

### config.db — `announcements`

```sql
CREATE TABLE announcements (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,              -- 100文字まで
    body        TEXT    NOT NULL,              -- 500文字まで
    image       BLOB    DEFAULT NULL,          -- PNG・JPEG・GIF・WebP、5MBまで
    image_name  TEXT    DEFAULT NULL,          -- image.<拡張子>（拡張子は中身から決める）
    link_url    TEXT    DEFAULT NULL,          -- http・httpsだけ
    link_label  TEXT    DEFAULT NULL,          -- NULL=「詳しくはこちら」
    starts_at   INTEGER DEFAULT NULL,          -- 表示開始（UNIX時刻。NULL=すぐ）
    expires_at  INTEGER DEFAULT NULL,          -- 表示終了（UNIX時刻。NULL=恒久）
    created_at  INTEGER NOT NULL DEFAULT (strftime('%s', 'now'))
)
```

Botの運営者が全サーバーに向けて出すお知らせ。サーバー設定と同じ`config.db`に置き、`AnnouncementRepository`が別の接続で扱う（既存の定時バックアップで守られる）。`expires_at`がNULLなら恒久、値があれば期間限定。期限が過ぎた行は起動時とお知らせを追加したときに消す。

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

`swap.preprocess_text(text, dictionary, emoji_ja, guild, attachments, mentions, author_id=None, user_readings=None, use_sounds=True)` は `PreprocessResult(text, replaced_ranges, sound_id, spaced_out)` を返す。`dictionary` は`DictionaryRepository`が生成した`DictionarySnapshot`、`user_readings`は`SpeechService`が`UserReadingService`で解決したユーザーIDと読み方のマッピング。`use_sounds=False`のときは手順1a・1bを飛ばす（`!t`で音声ファイルを作るとき）。`swap.py`はSQLite接続を受け取らない。

前処理と音声辞書の判定のあとに、読む文字（空白・区切り記号・制御文字以外）が残らなければ、`SpeechService`は音声を作らない（embedだけのメッセージ、カスタム絵文字だけのメッセージ、前処理で空になる`!t`など）。`speak_and_export()`はこのときNoneを返す。`VoicevoxClient.generate()`も、空白だけの文章はVOICEVOXへ送らずにNoneを返す。

`DictManager.preprocess_text(text, guild_id, guild, attachments, mentions, author_id=None, user_readings=None, use_sounds=True)` は辞書サービスの窓口であり、内部でRepositoryからスナップショットを取得して`swap.preprocess_text`を呼ぶ。最大文字数チェック・トリミングは前処理後に`SpeechService`が行う。

---

## 辞書のエクスポート・インポート（`/dict export`・`/dict import`）

形式は.dictファイル（CSV形式）で、1行に1件、`単語, 読み`と書く。書き出すファイルの名前は`exported_<guild_id>.dict`で、文字コードはUTF-8。

- 対象はそのサーバーの読み方を持つ項目だけ。音声辞書の設定と`__common__`は扱わない。`is_priority`は保存せず、読み込むときに単語から決め直す。
- 書き出しでは、読み方にカンマを含む項目を入れない（読み込むときに単語と読み方の境目が分からなくなるため）。
- 読み込むファイルの文字コードは`detect_encoding()`が中身から決める。BOMがあればそれに従い、なければUTF-16（半角文字の上位バイトの0の並び）、UTF-8、Shift_JIS（cp932）の順に、読めてNUL文字を含まないものを選ぶ。
- 読み込みでは、行の最後のカンマで単語と読み方に分ける。空の行と、`#`で始まりカンマのない行は読み飛ばす。
- 読み込むファイルは1MBまで、登録する単語は1回に5,000件まで。超えたとき、または文字コードを判別できないときは、ファイル全体を読み込まない。
- すでに読み方がある単語は上書きしない。音声辞書だけがある単語には、`sound_id`を残したまま読み方を足す。
- 書き込みは`DictionaryRepository.import_readings()`が1つのトランザクションで行い、途中で失敗したら何も登録しない。
- 読み込まなかった行は`行番号: 元の行 — 理由`の一覧にして、実行者へDMで送る。DMを送れなかったときは、実行者にだけ見える形で送る。
- 権限は、両方とも当面はサーバー管理者（`manage_guild`）に限る。
- v4.2.0.70ではベータ版の機能として扱い、両方のコマンドの説明の先頭に「（ベータ版）」と付ける。

---

## チャンネル判定ロジック（`services/message_service.py`）

```
temp_text_targets（一時設定）→ TextTarget（永続設定）の順で参照。
どちらも設定されていない場合: VCのテキストチャット（同一channel_id）のみ読む。
どちらかが設定されている場合: 設定チャンネル OR VCのテキストチャット のどちらかに一致すれば読む。
```

`GuildSession.temporary_text_channel_id` は `/join` 実行時にコマンドチャンネルへ設定する。`/setting text-target`を実行すると一時設定を消し、設定したチャンネルをすぐに読み上げ対象にする。VCのテキストチャットは一時設定や`TextTarget`に関係なく常に読む。`ConnectionService`はBotが強制切断された場合に`pending_text_channel_id`へ退避し、再接続時に復元する。自発的な切断は`voluntary_disconnects`で識別し、一時設定を破棄する。

**AutoJoinの抑止:**

AutoJoinは、`VoiceTarget`のVCへ人間ユーザーが入り、そのVCの人間が1人になったときだけ動く。Botが入ってきてもつながない。人が残っているVCからBotが抜けたとき（`/leave`・メンション・Discord上での切断など、理由を問わない）は、そのVCを`GuildSession.auto_join_suppressed_channel_id`に記録し、そのVCから人間がいなくなるか、Botがどこかへつなぐまで、誰が来てもAutoJoinしない。

**Botメッセージの扱い（2026-06-27 追加）:**  
Bot からのメッセージは通常 TTS をスキップするが、sounddict に一致する場合のみサウンドボードを再生する。チャンネル判定は人間ユーザーと同じロジック（`TextTarget` / VC テキストチャット）を適用。`SpeechService.add_message(sounddict_only=True)` で呼び出される。

---

## スラッシュコマンド

| コマンド | 引数 | 権限 |
|---|---|---|
| `/join` | — | VCにいるユーザー |
| `/leave` | — | VCにいるユーザー |
| `/version` | — | 全員 |
| `/clear` | `instant: bool` | 全員 |
| `/user-reading` | `reading: str (1〜50文字)` | 全員（DM可・ephemeral応答） |
| `/user-reading-reset` | — | 全員（DM可・ephemeral応答） |
| `/announce add` | `expires: str`, `starts: str`（タイトル・本文・画像・リンクはModalで入力） | `OPERATOR_USER_ID`に書いた人だけ（BotとのDMだけ） |
| `/announce edit` | `id`, `expires: str`, `starts: str`, `remove_image: bool`（中身はModalで直す） | 同上 |
| `/announce list` | — | 同上 |
| `/announce del` | `id` | 同上 |
| `/announce preview` | `id`（省くと今の接続メッセージ） | 同上 |
| `/synthesis-log` | `count: int (1〜500、既定はすべて)` | `OPERATOR_USER_ID`に書いた人だけ（BotとのDMだけに出す。運営者以外への断りだけephemeral応答） |
| `/tsumugi-tts` | `text: str` | 全員（BotがVCに接続しているときだけ） |
| `/t` | `text: str` | 全員（`/tsumugi-tts`の短縮） |
| `/skip` | — | 全員 |
| `/s` | — | 全員（`/skip`の短縮） |
| `/c` | — | 全員（`/clear instant:True`の短縮） |
| `/dict add` | `word`, `read` | 全員 |
| `/dict del` | `word`, `both: bool` | 全員 |
| `/dict view` | `search: str`, `ephemeral: bool` | 全員 |
| `/dict view-common` | `search: str`, `ephemeral: bool` | 全員 |
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
| `/setting max-char` | `chars: int (30〜500、0で既定の100へ戻す)` | manage_guild |
| `/setting auto-join` | `enabled: bool`, `channel: TextChannel`（有効にするとき`VoiceTarget`が未設定なら、実行者が入っているVCを記録する。VCに入っていなければ断る。`channel`を指定したらそのチャンネルを`TextTarget`にする。省略したときは、`TextTarget`が未設定なら実行したチャンネルを記録し、設定済みなら変えない。`TextTarget`を記録したら一時的な読み上げ先を消す） | manage_guild |
| `/setting access-notice` | `enabled: bool` | manage_guild |

メッセージトリガー:
- `@Bot` 単体メンション: VC接続トグル
- `s`: 現在再生中をスキップ
- `c`: 読み上げキューをすべてクリア（`/clear instant:True` と同じ）。クリアのたびに`GuildSession.clear_generation`を上げ、クリアの時点で音声を生成中だったメッセージも読み上げない
- `!s `・`!skip ` プレフィックスまたは silent フラグ: 読み上げをスキップ
- `!t `・`!tts ` プレフィックス: 続く文章を読み上げ、その音声をWAVファイルにしてメッセージへ返信する（`/tsumugi-tts`も同じ）。処理は`SpeechService.speak_and_export()`にまとめ、ふつうの読み上げと同じ設定・前処理・最大文字数を使うが、音声辞書には反応させない（`use_sounds=False`）。WAVは再生が終わると消えるので、キューへ入れる前に中身を読む。ファイル名は`YYYYMMDD-HHMMSS-<文章の先頭40文字>.wav`（日本時間）。クリアされたときは読み上げずにファイルだけ送り、メッセージが削除されたときはどちらもしない
- メッセージの削除: 読み上げ中なら止め、読み上げ待ちなら再生しない（`VoiceService.cancel_message()`）

---

## 外部HTTP API

- VOICEVOX通信は`VoicevoxClient`、Discord Soundboard通信は`DiscordSoundboardClient`だけが担当する。
- Discord Gateway接続前に`VoicevoxClient.check_health()`で`/version`へ疎通確認し、失敗した場合はBotの起動を中止する。このときは終了コード1で終わり、systemdの`Restart=on-failure`で10秒後に起動し直す（マシンの起動直後にVOICEVOXより先に起動しても、VOICEVOXが立ち上がればつながる）。
- 疎通確認のあと、`VoicevoxClient.warm_up_speaker()`が裏で`/initialize_speaker`を呼び、`DEFAULT_SPEAKER`の声のモデルだけを前もって読み込む。VOICEVOXは話者を最初に使うときにモデルを読み込むため、起動直後の最初の読み上げが遅れないようにする。合成用のロックは取らず、起動も待たせない。失敗しても表示するだけでBotは止めない。サーバーごとに設定した話者は読み込まない。
- 稼働中の音声生成は全ギルドで1件ずつ処理し、一時的なHTTP障害だけを、順番が来てから最大30秒再試行する。ほかの生成の順番を待った時間はこの30秒に数えない（長い文章の生成のあとに並んだメッセージが、待っただけで落ちないようにするため）。一時切断後はClient所有のHTTPセッションを再生成する。

### ストリーミングでの音声合成（`/streaming_synthesis`）

- 実験的な機能として、`.env`の`VOICEVOX_STREAMING`が`on`のときだけ使う（既定は`off`）。`config.py`が読み、`main.py`が`VoicevoxClient(streaming_enabled=...)`へ渡す。`off`のときも`generate()`は`StreamingAudio`を返すが、`/synthesis`で全体を作ってから返す（書き終えた状態）。
- `VoicevoxClient.generate()`は`StreamingAudio`を返す（作れなかったときはNone）。VOICEVOX Engine 0.26.0以降の`/streaming_synthesis`（`segment_length`は`STREAM_SEGMENT_SECONDS`＝1.0秒）を使い、最初の部分が届いた時点で返す。残りは裏のタスクで受け取り、`TMP_DIR`の同じWAVファイルへ書き足す。WAVのヘッダーには全体の大きさが最初から入っている。
- 生成の順番（全ギルドで1件ずつ）は、残りを受け取り終えるまで持ち続ける。最初の部分が届くまでは今までどおり再試行し、届いたあとに切れたときはやり直さずに失敗として記録・通知する（`VOICEVOX streaming`）。再生は届いた分までで終わる。
- 順番が来てから30秒の期限で縛るのは、最初の部分が届くまで。残りは長い文章ほど時間がかかるので全体の時間では縛らず、`STREAM_READ_TIMEOUT_SECONDS`（15秒）届かなかったときだけ打ち切る。
- エンジンが`/streaming_synthesis`を持たない（404・405）と分かったら、以後はBotを再起動するまで`/synthesis`を使う。話者がストリーミングに対応していない（422）ときは、その話者だけ`/synthesis`を使う。どちらも全体を作ってから、書き終えた`StreamingAudio`を返す。
- `VoiceService.play()`は、書き終えていない音声を`StreamingAudio.open_reader()`で開き、`discord.FFmpegOpusAudio(reader, pipe=True, before_options="-f wav")`へ渡す。読み手は届いていない分を待ち、書き終えたら残りを読んで終わる。30秒届かなければ打ち切る。再生が終わったとき・再生しなかったときは読み手を閉じ、待っているスレッドを終わらせる。書き終えた音声は今までどおり`from_probe()`でファイルから再生する。
- クリア・削除・`!t`の扱いは今までと同じ。WAVファイルは書きかけでも同じ名前にあるので、消す処理はそのまま動く。`speak_and_export()`はファイルにするため、最後まで届くのを待ち、途中で切れたらNoneを返す。

### 生成の記録（`/synthesis-log`）

- `GenerationLogService`が、VOICEVOXの生成の記録を直近500件までメモリに持つ。DBには書かず、Botを再起動すると消える。運営者が発言の内容を推し量れないよう、読み上げた文章と、どのサーバーの生成か（サーバーID・サーバー名）は持たず、文字数だけを残す。
- 成功・失敗は`VoicevoxClient.generate()`が記録する。時間は、生成の順番（全ギルドで1件ずつ）を待った時間と、順番が来てから生成し終える（失敗なら諦める）までの時間に分け、試した回数と、失敗なら例外の名前も残す。ストリーミングしたかも残す。ストリーミングの成功は、残りを受け取り終えたときに記録する。
- 記録には`first`（メッセージを受け取ってから最初の音を再生するまでの時間から、生成の順番を待った時間を除いたもの）も残す。数え始めは、メッセージがこのプログラムに届いた時刻（`PlaybackCog`の`on_message`に入った時刻。`/tsumugi-tts`・`/t`はコマンドの処理に入った時刻）で、`MessageService.handle()`から`SpeechService.add_message()`・`speak_and_export()`へ`received_at`として渡す（渡されなかったときは、それぞれの始めの時刻を使う）。`SpeechService`がこの時刻を、`VoicevoxClient`が順番を待った時間を`StreamingAudio`に書き、`VoiceService`が`FirstPacketSource`で最初の音のデータをDiscordへ渡した時刻を知らせる。ストリーミングでは記録より先に再生が始まるので、`StreamingAudio.attach_record()`・`mark_played()`のどちらが先でも記録に書き込まれる。同じサーバーで前の読み上げが終わるのを待った時間は含む。入退室通知など、メッセージから作っていない音声と、再生しなかったものは空欄にする。
- スキップは`SpeechService`が記録する。理由は、読む文字がない（`empty`）、生成中にクリアされた（`cleared`）、生成中にメッセージが消された（`deleted`）の3つ。クリア・削除のスキップは、生成の成功とは別の記録として残る（生成の時間は使っているため）。生成を始める前にクリア中だったメッセージと、`!t`でクリアされてファイルだけを送ったものは記録しない。
- `/synthesis-log`は、記録全体の集計をEmbedで表示し、直近`count`件の一覧（新しい順）を`synthesis-log-YYYYMMDD-HHMMSS.txt`（日本時間）として添付する。一覧は`datetime | result | chars | synthesis | first | wait | speaker | note`の列をそろえた表で、`note`にはストリーミング（`stream`）、試した回数（2回以上）、スキップの理由・失敗した例外の名前を書く。Embedの集計は日本語で、表の列に合わせて「件数」（スキップの理由ごとの内訳）、「生成時間（成功のみ）」（平均と1文字あたり・中央値・95パーセンタイル・最大）、「最初の音まで」（`first`の平均・中央値・最大とストリーミングした件数）、「順番待ち」（平均・最大）の4つにする。記録がないときは「記録はまだありません」とだけ書き、ファイルは添付しない。サーバーの情報は表示しない。
- 両Clientは`aiohttp.ClientSession`を初回通信時に生成して再利用する。通常実行経路で同期`requests`は使用しない。
- `VoiceService`と`UpdateSoundBoards`はHTTPのURL・認証方法を知らず、Clientのメソッドだけを呼ぶ。
- Bot終了時は`ManagedDiscordClient.close()`がHTTPセッションと5つのSQLite接続（`config.db`は設定とお知らせの2つ）を閉じる。

---

## Components v2への移行（`presentation/layouts.py`）

Embedで送っているメッセージを、Components v2（`discord.ui.LayoutView`）へ順に移す。`ConnectionService`が送るメッセージ（接続・切断・自動退出・権限エラー・接続失敗）と、`cogs/announcement_cog.py`が送るメッセージ（`/announce`の応答・確認・エラー・入力し直すボタン・一覧・削除）はすべてv2にした。ほか（`ReconnectService`の再起動の知らせ・再接続、`presentation/error_handler.py`のエラー応答など）は当面Embedのまま残す。

- `presentation/layouts.py`は`presentation/embeds.py`と同じく、汎用のひな形だけを持つ。`make_layout(title, description, *, layout_type, header, view)`は、色の付いた枠（`Container`）に`## 見出し`と本文を入れた`LayoutMessage(view, container, files)`を返す。`header`は枠の外（上）に出す一言。`view`には、ボタンの処理や期限を持つ`LayoutView`の派生を渡せる（省くと期限なしの`LayoutView`を作る）。
- 中の部品は、呼び出し側が`LayoutMessage.add_item()`（Embedの`add_field`に当たる）で枠の最後に足し、`attachment://`で参照するファイルは`add_file()`で足す。ある機能のための部品の組み立ては、`presentation/announcement_layout.py`のように別のファイルに分け、`layouts.py`に機能ごとの引数を足さない。
- 送るときは`send(view=..., files=...)`、`/join`のように`defer()`したあとは`edit_original_response(view=..., attachments=...)`を使う。v2のメッセージには`content`・`embed`を一緒に付けられない。
- 枠の色は`embed_color()`で、同じ場面のEmbedと同じ色にする。
- `discord.File`は一度しか送れないので、送るたびに作り直す。
- Presentationは`services/`に依存しない（`presentation/error_handler.py`のエラー通知を除く）。ServiceとPresentationの両方で使う値は`models/`に置く。

### 接続したときのメッセージ

| 場面 | 見出し | 枠の色 |
|---|---|---|
| `/join`・`@Bot`単体メンション | 接続しました | 緑（`SUCCESS`） |
| AutoJoin | 自動で接続しました | 青（`INFO`） |

本文はどれも`{VCのメンション}に接続しました。`の1行。読み上げチャンネルはこのメッセージを出したチャンネルなので書かない。再起動のあとのつなぎ直し、切断・エラーのメッセージにはお知らせを載せない。

切断・自動退出・権限エラー・接続失敗のメッセージも、`make_layout()`の枠に見出しと本文だけを入れて送る（色は今までのEmbedと同じ）。Embedのときと同じく、送るところで`make_layout()`を呼んで`send(view=..., files=...)`（`defer()`したあとは`edit_original_response(view=..., attachments=...)`）で送る。部品を足すときは、送る前に`add_item()`・`add_file()`を呼ぶ。

### 運営者からのお知らせ（`/announce`）

- `ConnectionService.connected_message()`が`make_layout()`で枠を作り、`add_announcements()`でお知らせ欄を足す。`/announce`のプレビューも同じ組み合わせで作る。
- 接続したときのメッセージで、本文の下にお知らせを並べる。「お知らせ」のような見出しは付けず、お知らせごとに区切り線（`Separator`、`large`）を引く。お知らせがないときは区切り線も出さない。
- 1件のお知らせは、`## タイトル`と本文を1つの`TextDisplay`にまとめる。期限は表示しない。リンクがあれば`Section`にし、リンクボタンを右（`accessory`）に置く。画像はその下に置く。
- 並び順は、恒久（古い順）のあとに期間限定（期限の近い順）。載せるのは3件までで、超えた分は「ほか N 件のお知らせがあります」とまとめる。期間限定のものは、期限まで接続のたびに毎回出す。
- 画像は`MediaGallery`（`attachment://`でファイルを参照）で出す。ファイルを添付する権限（`attach_files`）がないチャンネルでは、画像だけを省く。お知らせを読めなかったときは、エラーを知らせてお知らせなしで送る。
- `/announce add`・`edit`は、期間（`expires`・`starts`）をコマンドの引数で受け取り、タイトル・本文・画像・リンクURL・リンクの表示名の5つをModal（`Label`・`TextInput`・`FileUpload`）で受け取る。`expires`を省くと恒久。日時は日本時間の`YYYY-MM-DD HH:MM`（`/`区切り・時刻の省略も可）か、`3d`・`12h`・`30m`（組み合わせ可）。`starts`を指定したときは、`expires`の`3d`などは`starts`から数える。`edit`では省いた引数は今の値のままで、`none`で外す。
- 編集のModalには今の値を入れておく。ファイル欄には今の画像を入れておけないので、空欄なら今の画像のまま、選べば差し替え、`remove_image:True`で消す（新しい画像を選んだときはそちらを使う）。
- 画像は送信を受けた時点でダウンロードしてDBに保存する（Discordの添付のURLはしばらくすると開けなくなるため）。大きさは読み込む前に確かめ、形式は中身の先頭のバイト列で決める。
- 入力に誤りがあったら、理由と「入力し直す」ボタンを返す。ボタンは入力した値を残したままModalを開き直す（画像は選び直す）。
- Modalを送信しただけでは保存しない。入力を確かめたら（`AnnouncementService.check()`）、接続メッセージと同じ形のプレビューの下（枠の外）に「送信する」「内容を変更する」「破棄する」のボタンを付けて返す（`ConfirmView`・`AnnouncementConfirm`）。ボタンは`OPERATOR_USER_ID`に書いた人だけ（複数人なら誰でも）が押せる。
  - 「送信する」で初めて追加・更新し、同じメッセージを「ID N のお知らせを追加しました（更新しました）」のプレビューに置き換える。保存するときにもう一度確かめ、確認しているあいだに表示終了が過ぎたときなどは、理由と「入力し直す」ボタンを返す。
  - 「内容を変更する」は、入力した値と選んだ画像を残したままModalを開き直す。新しい内容の確認を出したら、前の確認は「内容を変更しました」に置き換えてボタンを外す。Modalを閉じただけなら、前の確認はそのまま使える。
  - 「破棄する」は保存せず、メッセージを「お知らせを破棄しました」に置き換える。
  - 15分（`FORM_TIMEOUT_SECONDS`）押されなければ保存せず、メッセージを「確認の期限が切れました」に置き換える。
  - 送信・破棄・変更・期限切れのどれかで終わった確認のボタンは、押しても何もせず、使えないことだけを本人に知らせる（二重に保存しない）。メッセージを置き換えるときは、画像も添付し直す。
- 入力の誤りは、赤の枠に理由を入れ、「入力し直す」ボタン（`RetryView`）を枠の外（下）に置く。
- `/announce list`は、v2のメッセージの上限（文字の合計4000文字・部品40個）に収めるため、全件を区切り線の下の1つの`TextDisplay`にまとめ、各お知らせは`### ID N｜タイトル`・期間と画像・リンクの有無・本文の先頭60文字にする。文字数が`LIST_TEXT_BUDGET`（3500文字）を超える分は「ほか N 件は表示しきれませんでした」とまとめる。

---

## 起動の準備完了の知らせ

`on_ready`の処理（コマンドの同期、ギルドの整理、Soundboardの更新、バックアップの開始、ステータスの更新）がすべて終わったあとに、`ReadyStateService.mark_ready()`が`UPDATE_STATE_DIR/ready.json`へバージョン・プロセスID・時刻を書く。一時ファイルから置き換えるので、書きかけを読まれることはない。Botの終了時には`ManagedDiscordClient.close()`の中でこのファイルを消す。書き込みに失敗してもBotは止めない。インストーラーは起動前にこのファイルを消し、サービスの今のプロセスIDと期待するバージョンが書かれるまで最大60秒待つ。

## 更新の見える化（`services/update_watch_service.py`）

`UPDATE_STATE_DIR`が空欄でなければ、`on_ready`の処理のあとに`UpdateWatchService`が動き出し、5秒おきに`UPDATE_STATE_DIR`を見る。

- インストーラーは、更新（手でも自動でも、`repair`も）を始めるときに`updating.json`（インストーラーのプロセスID・前後のバージョン）を置き、終わったときと、新しいバージョンのBotを起動する直前に消す。Botはこのファイルがあるあいだ、ステータスを「取り込み中」と「📥アップデート中…　操作を受け付けない場合があります」にし、なくなったら元の表示に戻す。書いたプロセスがもういなければ、残ったファイルとして無視する。
- `StatusService.set_override()`の表示は`clear_override()`まで、ふだんのステータス更新で上書きしない。
- `last-update.json`（更新・入れ直し・戻したときの結果）を見張りのたびに読み、まだ知らせていなければ`OPERATOR_USER_ID`の全員へEmbedでDMする。インストーラーは新しいバージョンのBotの起動を確かめてから結果を書くので、起動したときに1回読むだけでは間に合わない。成功も知らせる。知らせたら`update-notice.json`に印を付け、同じ結果を二度送らない。DMを1人にも送れなかったときは、次の見張りでもう一度送る（1人にでも送れたら知らせたものとし、送れた人へ二度送らない）。
- `auto-update.json`の結果が`unfinished`（前回の更新が途中で止まっている）になったら、運営者へDMする。同じ状態が続くあいだは1回だけにし、ほかの結果に変わったら印を消す。
- `available-update.json`に今と違うバージョンがあり、まだ知らせていなければ、運営者へ「今すぐ更新する」「VCが空いたら更新する」のボタン付きでDMする（同じバージョンは1回だけ）。ボタンは`UpdateButton`（`discord.ui.DynamicItem`。custom_idにバージョンを入れ、再起動したあとも押せる）で、押されたら`client.update_watch`が受ける。押せるのは`OPERATOR_USER_ID`に書いた人だけ（複数人なら誰でもよい）。「今すぐ」は`update-request.json`に依頼を書く。「VCが空いたら」は`pending-update.json`に残し、BotがどのVCにもつないでいないときに依頼を書く。今のバージョンと同じか、`available-update.json`と違うバージョンのボタンは、依頼を書かずに知らせる。お知らせを送ったら、運営者ごとのメッセージIDを`update-notice.json`の`offered_messages`に覚えておく。ボタンが押されたら、押されたDMとほかの運営者へ送った同じお知らせを`button_message`（`version`と`messages`の一覧）に覚えておき、更新が始まった（`updating.json`が現れた）ら「更新を開始しました」に書き換える。見張りのすきまで更新が終わったときは、新しいバージョンで起動したあとに書き換える。書き換えられなかったDMだけを残し、次の見張りでもう一度試す。運営者が1人だった版の`button_message`（`message_id`だけ）は、`OPERATOR_USER_ID`の先頭の人のDMとして扱う。

## 止めるときと、再起動のあとのつなぎ直し（`services/reconnect_service.py`）

`main.py`は`ManagedDiscordClient.run_until_stopped()`でBotを動かす。`discord.Client.run()`はSIGTERMでDiscordとの接続を閉じずに終わり、終了前の処理が動かず、古い接続がしばらく残ってステータスも混ざるため、SIGTERM・SIGINTを受けたら`close()`を呼ぶ。`close()`は、登録した終了前の処理を動かしてからDiscordとの接続を閉じる。

- `ReconnectService`は、つないでいるVC（サーバー・VC・一時的な読み上げチャンネル）を`UPDATE_STATE_DIR/voice-sessions.json`へ、Bot自身がVCにつないだ・抜けたときと、1分おきに書く。最後に動いていた時刻（`alive_at`）も書くので、異常で落ちたときも止まっていた時間がわかる。起動したときのつなぎ直しが終わるまでは書かない。
- インストーラーはBotを止める前に、`restart-reason.json`へ止める理由を書く。更新・`rollback`は`update`、`tsumugium-setup stop`・`uninstall`は`manual_stop`。ファイルがなければ再起動（`systemctl restart`、マシンの再起動、異常で落ちたあとの自動の起動）として扱う。
- 止めるとき（終了前の処理）は、理由が`manual_stop`なら`voice-sessions.json`を消して黙ってVCを抜ける。それ以外は、最後の状態を書いてから、読み上げチャンネル（一時的な読み上げチャンネル→`TextTarget`→VCのテキストチャットの順に最初にあるもの）へ「アップデートのため再起動しています」か「（Bot名）は再起動しています」を送り、VCを抜ける。知らせと切断は、合わせて最大10秒で打ち切る。この切断は自発的な切断として扱う。
- 起動したときは、`on_ready`の処理のあとに`restart-reason.json`を読んで消す。理由が`manual_stop`でなく、`alive_at`から3分以内なら、記録したVCのうち人がいるVCにつなぎ直し、一時的な読み上げチャンネルを戻して「再接続しました」を送る。すでにつないでいるサーバーと、人がいないVCは飛ばす。

---

## インストーラー（`installer/`）

サーバーへの導入・更新・管理を行う`tsumugium-setup`の本体。標準ライブラリだけで動き、Python 3.10以上を前提にする。利用者向けの手順は[`docs/INSTALL.md`](docs/INSTALL.md)、更新の流れは[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)を参照。

| ファイル | 役割 |
|---|---|
| `install.sh` | 展開したzipから最初に実行する入口。Python 3.10以上かを確かめ、隣の`installer/`を呼ぶ |
| `installer/cli.py` | 9つのモードの振り分け。想定している失敗は理由を表示して終了コード1で終える |
| `installer/install.py` | `install`モード |
| `installer/upgrade.py` | `upgrade`・`repair`モード。取得・照合・展開までを今のバージョンが行い、そのあとを新しいバージョンに引き継ぐ |
| `installer/handoff.py` | 引き継ぎを受ける新しいバージョンの入口 |
| `installer/rollback.py` | `rollback`モード。`app.prev/`にある1つ前のバージョンのインストーラーで動く |
| `installer/manage.py` | `status`・`start`・`stop`・`uninstall`モード |
| `installer/autoupdate.py` | 自動更新のtimerとservice、`auto-update`モード（on・off）、更新の結果（`data/state/last-update.json`・`auto-update.json`）の書き残し。更新するかの判断は`upgrade.py`の`upgrade --auto`が行う |
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
| `scripts/release_notes.py` | GitHub Releaseの本文（正式版は見出しのひな形と前の正式版からのコミット名の一覧、ベータ版は前のタグからのコミット名の一覧）を作り、タグのあとでコードが変わったかを調べる。PRでコードを変えたのに`VERSION`を上げ忘れていないかも調べる（CIで使う） |
| `scripts/ci_installer_e2e.py` | CIの結合テスト。本物のsystemdでinstallからuninstallまでを動かす |
| `scripts/migrate_legacy.py` | 旧環境から`.env`と`data/`を作る移行ツール。配布用のzipには入れず、移行する人に直接渡す |

- 新しいバージョンへ引き継ぐ形式には番号（`installer/__init__.py`の`HANDOFF_VERSION`）を付ける。引き継ぐ項目の意味を変えるときは番号を上げる。知らない番号を受け取ったバージョンは、何も変えずに止まる。
- インストーラーを別のプロセスで動かすときは、`python -m installer`を使わず、`installer.python_command()`（`python -I`で起動する）を使う。`-m`は今いるディレクトリの`installer/`を先に読むので、展開したzipやリポジトリの中で実行すると、違うバージョンが動くため。
- インストーラーが作るunitと入口には`# Managed by tsumugium-setup`の印を付ける。印のない同じ名前のファイルは、上書きも削除もしない。
- `main`にコミットが入ると、CIの`release`ジョブが`build`ジョブの配布物を添付したGitHub Releaseを作る。`VERSION`が正式版なら下書きを作り、ベータ版（`(beta X)`）ならタグ`vX.X.X.XX-beta.XXX`（ベータ番号は3桁にゼロ埋め）のプレリリースをすぐ公開して、新しいほうから5件を残して古いプレリリースとタグを消す。同じタグか下書きがすでにあれば作らず、そのタグのあとでコードが変わっていれば、`(beta X)`の上げ忘れとして警告を出す。書き込みの権限（`contents: write`）は、このジョブだけに付ける。
- PRですべての確認が通ると、CIの`verified`ジョブが、確かめた中身（マージした結果のツリー）を`verified-tree-<ツリー>`という名前の成果物に残す。mainへのpushでは、`gate`ジョブがこのコミットと同じ中身の記録（同じリポジトリのPRのもの）を探し、見つかればテスト・静的チェック・インストーラーの結合テスト・秘密情報のスキャンを省いて、`build`・`lock-check`・`release`だけを動かす。`build`は`build-info.json`にコミットを書くため、省かない。記録にはPRで確かめた`requirements.lock`も入れ、マージ後に作り直したロックがこれとまったく同じなら`lock-check`も省く（依存は範囲指定なので、違うロックになることがある）。見つからないとき（PRを確かめたあとにmainが進んだ、mainへ直接pushした、など）は、すべて確かめる。
- 配布用のzipには`.claude/`・`.github/`・`docs/`・`scripts/`・`tests/`・`.gitignore`・`AGENTS.md`を入れず、`build-info.json`と`requirements.lock`を加える。
- `requirements.lock`は、CIで`uv pip compile --universal`を使ってハッシュ値つきで作る。`--universal`を付けると、Python 3.13以上だけで要る依存（`audioop-lts`など）も条件付きで書き出されるので、Python 3.10以上のどのバージョンでも同じロックを使える。インストーラーは`pip install --require-hashes`で入れる。CIの`lock-check`ジョブがPython 3.10〜3.14のそれぞれで入れられることを確かめる。
- 自動更新は、`<サービス名>-update.timer`が10分おきに`upgrade --auto`を動かす。既定は`beta`チャンネルならオン、`stable`ならオフ（記録の`auto_update`がないときもチャンネルで決める）。起動を確かめられずに戻したバージョンと、`rollback`で戻した元のバージョンは、記録の`skip_versions`に入れて自動更新では入れない。
- 自動更新がオフでもtimerは止めない。オフのときは、新しいバージョンを見つけたら`data/state/available-update.json`に書くだけにし、新しいバージョンがなければ消す。Botの「更新する」ボタンは`data/state/update-request.json`に依頼を書き、`<サービス名>-update.path`（`PathChanged=`）が同じserviceを動かす。`upgrade --auto`は依頼を先に扱い、頼まれたバージョンがReleasesにあり、今より新しく、チャンネルで入れられるときだけ更新する（飛ばすバージョンでも入れる）。ほかの操作が実行中のときは依頼を残し、次の確認に回す。それ以外は依頼を消す。
- 記録の形式を変えるときは、`installer/instance.py`の`RECORD_FORMAT`を上げる。知らない項目は読み飛ばすので、項目を足すだけなら上げなくてよい。

---

## 音声再生アーキテクチャ（`services/voice_service.py`）

`VoiceService.sessions: dict[int, GuildSession]` でギルドごとの実行状態を保持する。`GuildSession.queue` に音声アイテムを積み、`play_loop` が順次消費する。キュー投入は `VoiceService.enqueue()` に集約する。

`GuildSession` はキューのほか、再生タスク、キープアライブタスク、一時テキストチャンネル、再接続時の復元待ちチャンネル、スキップ・クリア状態を保持する。

### キューアイテム形式

| 型 | 形式 | 説明 |
|---|---|---|
| TTS | `TTSItem(path: str, message_id, audio: StreamingAudio \| None)` | VOICEVOX 生成 WAV ファイルパス。`audio`があれば書き終える前でも届いた分から再生する |
| Soundboard | `SoundboardItem(sound_id: str)` | Discord サウンドボード |

`isinstance(item, TTSItem)` / `isinstance(item, SoundboardItem)` で判別する。

### `play_loop` の処理フロー

1. キューからアイテムを取り出す（300秒タイムアウト）
2. `item` が `SoundboardItem` の場合:
   - `voice_client.is_playing()` が False になるまで 0.1秒ポーリングで待機（TTS 完了待ち）
   - `_play_soundboard()` で Discord API を呼び出し、即 `task_done()`
3. `item` が `TTSItem` の場合: `play()` で FFmpeg 再生、完了後 `task_done()`（書きかけの音声はパイプで渡す）

Botが接続し直したときは、`VoiceService.reset_speaking_soon()`が1秒後に無音のOpusフレームを5つ送ってから「話していない」状態を送る。話している途中で強制切断されたあと、Discord上で「話している」表示が残らないようにするため。

**注意:** サウンドボード同士は互いに待機しない（Discord API は再生完了イベントを返さないため）。サウンドボード再生中でも次のアイテムは即時処理される。

### `/clear` の動作

キュードレイン時に `TTSItem` のみ `safe_remove()` でファイル削除。`SoundboardItem` はスキップ。

---

## ブランチ名

ブランチ名は`<種類>/<内容>`とし、名前だけで何の作業かが分かるようにする。

| 種類 | 使う場面 |
|---|---|
| `feat` | 機能の追加・変更 |
| `fix` | 不具合の修正 |
| `ci` | CI・リリースの仕組みの変更 |
| `docs` | ドキュメントだけの変更 |
| `refactor` | 動きを変えないコードの整理 |
| `test` | テストだけの変更 |
| `release` | リリースの準備（`release/vX.X.X.XX`） |

- `<内容>`は英小文字・数字・ハイフンで、2〜4語ほどにまとめる（例: `feat/update-button`、`fix/update-notice-timing`）。
- コミットメッセージの先頭（`feat:`・`fix:`など）と種類をそろえる。
- `claude/youthful-turing-snic0z`のような、内容の分からない自動の名前は使わない。Claude Codeなどで自動の名前が付いたときは、作業を始める前に上の形のブランチを作り直してから、そちらへコミット・pushする。

---

## バージョン表記

- 正式リリースのバージョンは`vX.X.X.XX`で表記する（例: `v4.1.0.69`）。先頭3つの`X.X.X`は[セマンティックバージョニング](https://semver.org/lang/ja/)に従う。末尾の`XX`は累積リリース回数であり、リリースのたびに1ずつ増やし、`X.X.X`が上がってもリセットしない。
- ベータ版は`vX.X.X.XX (beta X)`で表記する。
  - 前半の`vX.X.X.XX`には、次回リリースで使う予定のバージョンを書く。
  - 後半の`(beta X)`には、前回リリース以降に公開したベータ版の通し番号を1から書く。コミットの数ではなく、ベータ版を1回出すごとに1つ上げる。
  - ベータ版は、コードを書き換えた変更が`main`に入るたびに1つ公開される。そのため、PR（ブランチ）の中でコードを最初に書き換えるコミットで、`main`にある番号より1つ上げ、同じPRのそれ以降のコミットでは上げない。ドキュメントやテストだけのPRでは上げない。
  - 先にほかのPRが`main`に入って同じ番号を使ったときは、`main`を取り込んで番号を上げ直してからマージする（同じ番号のまま入れると、タグがすでにあるのでベータ版が出ない）。
  - 上げ忘れはCIの`静的チェック`で止める。PRのマージ先から配布物のコード（`scripts/release_notes.py`の`is_code()`。ドキュメント・テスト・`scripts/`・`.github/`などは含まない）が変わっているのに、`VERSION`がマージ先より新しくなっていなければ失敗にする（`release_notes.py --check-version-bump`）。マージ先が同じ番号まで進んだときも失敗になる。
  - ベータ番号はリリースごとにリセットし、次のリリースに向けた最初のベータ版を`(beta 1)`とする。
- 例: v4.1.0.69をリリースした後、次期リリースをマイナーバージョンアップとする場合、次期バージョンはv4.2.0.70となる。このとき最初にコードを書き換えたPRは`v4.2.0.70 (beta 1)`、3つ目のPRは`v4.2.0.70 (beta 3)`となる（それぞれのPRにいくつコミットがあっても、番号は1つだけ上がる）。v4.2.0.70のリリース後は、次期バージョンで再び`(beta 1)`から数える。
- GitHubのタグ名とReleaseのタイトルは、ベータ版では`vX.X.X.XX-beta.XXX`と表記し、ベータ番号を3桁にゼロ埋めする（例: `v4.3.0.71-beta.001`、`v4.3.0.71-beta.010`）。Gitのタグ名には空白を使えず、GitHubのReleasesはタグ名の文字列順で並ぶため、ゼロ埋めで桁をそろえて順番を保つ。タイトルはタグ名と同じ文字列にする。
  - `config.py`の`VERSION`と`/version`の表示は、ゼロ埋めしない`vX.X.X.XX (beta X)`のままとする（例: `v4.3.0.71 (beta 10)`）。
  - 正式リリースのタグ名とタイトルは`vX.X.X.XX`とする（例: `v4.3.0.71`）。

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
| `OPERATOR_USER_ID` | `""` | エラー・更新のDMを受け取り、運営者用のコマンドとボタンを使える人のDiscordユーザーID。カンマ区切りで複数人を書ける（全員へ送り、誰でも使える）。読めない項目は表示して飛ばす。未設定・空欄で無効。`config.py`は`OPERATOR_USER_IDS`（タプル）として公開する |
| `VOICEVOX_URL` | `http://127.0.0.1:50021` | VOICEVOX エンドポイント |
| `DEFAULT_SPEAKER` | `8` | デフォルト話者ID（Speaker=NULL時に使用） |
| `VOICEVOX_STREAMING` | `off` | 実験的な機能。`on`なら`/streaming_synthesis`で、音声を作り終える前に読み上げを始める。`on`・`off`のほか`true`・`false`・`1`・`0`・`yes`・`no`を読み、読めない値は表示してオフにする |
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
