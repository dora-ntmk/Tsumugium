# Tsumugium ユーザーガイド

v3.6.0.68（2026年8月31日）時点の情報です。

Botの応答・Embed・コマンド説明は日本語固定で、コマンド名のローカライズも行いません。旧バージョンの`/setting language`は廃止されています。

## 接続と切断

### `/join [change_channel]`

コマンド実行者が参加しているボイスチャンネルへBotを接続します。

- `change_channel=False`（既定）: コマンドを実行したチャンネルを、今回の接続中だけ読み上げ対象へ追加します。
- `change_channel=True`: サーバー管理権限がある場合、実行チャンネルを`TextTarget`、参加中のVCを`VoiceTarget`として保存します。

Botが別のVCへ接続済みの場合は、現在の接続を切断してから指定VCへ接続し直します。

### `/leave`

Botをボイスチャンネルから切断します。コマンド実行者もVCへ参加している必要があります。

### Bot単体メンション

メッセージ本文がBotへのメンションだけの場合、接続状態を切り替えます。

| Botの状態 | 動作 |
|---|---|
| VC未接続 | 投稿者のVCへ接続し、投稿チャンネルを一時的な読み上げ対象にする |
| VC接続中 | `/leave`と同様に切断する |

### 自動接続・自動退出

`VoiceTarget`と`AutoJoin`が設定されている場合、最初のユーザーが対象VCへ入室するとBotも接続します。

Botと同じVCから人間ユーザーがいなくなると、`AutoJoin`の設定にかかわらず自動退出します。

## 読み上げ

### 対象チャンネル

BotがVCへ接続している間、以下のメッセージを読み上げます。

- Botが接続しているVCの付属テキストチャット
- `/join`で一時設定されたテキストチャンネル
- 一時設定がない場合は`/setting text-target`で保存されたチャンネル

一時設定は永続設定より優先されます。BotがDiscord側から強制切断された場合は一時設定を退避し、再接続時に復元します。`/leave`などの自発的な切断では破棄します。

### 読み上げを行わないメッセージ

- `!s `で始まるメッセージ
- Discordのサイレント送信フラグが付いたメッセージ
- 対象チャンネル外のメッセージ

Bot自身を含むBot投稿は通常TTSしません。ただし音声辞書に一致した場合はSoundboardだけを再生します。

### 現在の音声をスキップ

対象チャンネルへ`s`だけを投稿すると、現在再生中のTTSを停止します。

### `/clear [instant]`

待機中の読み上げキューをすべて削除します。

- `instant=True`（既定）: 現在のTTSも停止します。
- `instant=False`: 現在のTTSは最後まで再生し、待機中の項目だけを削除します。

## 読み辞書

### `/dict add <word> <read>`

単語と読み方を登録します。読み方は50文字以内です。同じ単語が存在する場合は読み方を上書きします。

### `/dict del <word> [both]`

読み辞書から単語を削除します。`both=True`の場合、同じ単語の音声辞書設定も削除します。

### `/dict view [search] [ephemeral]`

登録内容を20件ずつ表示します。

- `search`: 単語または読み方の部分一致検索
- `ephemeral=True`: 実行者にだけ表示
- 📚: 通常辞書
- ⭐: 優先辞書
- ◀️ / ▶️: ページ移動

`guild_id='__common__'`の辞書は全サーバー共通辞書として読み上げ時に適用されます。

## 音声辞書

Discordサーバーへ登録済みのSoundboard音声を、特定のメッセージから再生します。

### `/sounddict add <word> <sound> [read] [full_match] [trigger_user]`

- `word`: 再生条件となる単語
- `sound`: 候補から選ぶSoundboard音声
- `read`: 同じ単語へ読み方も設定する場合に指定
- `full_match=True`（既定）: メッセージ全文が単語と一致した場合だけ再生
- `full_match=False`: メッセージ中に単語が含まれていれば再生
- `trigger_user`: 指定したユーザーまたはBotの投稿時だけ再生

完全一致の登録を先に評価し、その後に部分一致を評価します。条件に一致してSoundboardを再生したメッセージはTTSしません。

### `/sounddict del <word> [both]`

音声辞書から単語を削除します。`both=True`の場合、読み辞書からも削除します。

### `/sounddict view [search] [ephemeral]`

音声辞書を表示します。部分一致・発言者限定の条件も一覧へ表示されます。

## サーバー設定

`/setting`コマンド群はサーバー管理権限が必要です。

| コマンド | 内容 | 有効範囲・初期値 |
|---|---|---|
| `/setting view` | 現在の設定を表示 | — |
| `/setting text-target [channel]` | 永続的な読み上げチャンネル | 省略時は実行チャンネル |
| `/setting text-target-reset` | 読み上げチャンネルを未設定へ戻す | — |
| `/setting voice-target [channel]` | AutoJoin対象VC | 省略時は実行者が参加中のVC |
| `/setting voice-target-reset` | AutoJoin対象VCを未設定へ戻す | — |
| `/setting speaker <speaker>` | VOICEVOX話者 | Botのデフォルト |
| `/setting volume <volume>` | 音量 | 0〜100、初期値100 |
| `/setting speed <speed>` | 読み上げ速度 | 50〜200、初期値100 |
| `/setting max-char <chars>` | 最大読み上げ文字数 | 30〜200、`0`で50へ戻す |
| `/setting auto-join <enabled>` | 対象VCへの自動接続 | 初期値False |
| `/setting access-notice <enabled>` | VC入退室を音声で通知 | 初期値False |

`Speaker`が「Botのデフォルト」の場合、Bot管理者が設定した`DEFAULT_SPEAKER`を使用します。

## テキスト前処理の概要

読み上げ前に、主に以下を順番に処理します。

1. Soundboard完全一致・部分一致
2. 優先辞書
3. URL、カスタム絵文字、`www`
4. Markdown、タイムスタンプ
5. ユーザー・チャンネル・ロールメンション・ゲームメンション
6. 通常辞書と共通辞書
7. 絵文字の日本語短縮名
8. 添付ファイルの説明
9. 最大文字数の省略

優先辞書はURLやメンションなどの標準変換より先に適用されます。

## バージョン表示

`/version`でTsumugiumのバージョンと最終更新日を確認できます。
