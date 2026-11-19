# Components v2 表示対応表

Botが出すメッセージを、表示の形式（テキスト・Embed・Components v2）ごとに分け、分野別に並べた表です。Components v2への移し替えがどこまで進んだかを確かめるために使います。移し替えの方針は[`AGENTS.md`](../AGENTS.md)の「Components v2への移行」を参照してください。

調べた時点: `main`（cf58255、`/announce`をv2にしたあと）

## 数え方

- **1パターン**は、見出しと場面が同じメッセージを1つと数えます。同じ見出しでも、場面（失敗の理由など）が違えば別に数えます。`/join`とメンションのように、同じ文面を別のコマンドから出すものは1つにまとめます。
- **テキスト**は、`content`だけ、またはファイルだけを送るメッセージです。
- **Embed**は`make_embed()`（`discord.Embed`）で送るメッセージです。ページ送りや更新のボタン（旧来の`discord.ui.View`）が付くものも含みます。
- **Components v2**は`make_layout()`（`discord.ui.LayoutView`）で送るメッセージです。
- Modal（`/announce`の入力フォーム）はメッセージではないので数えません。
- **交換率**は、その分野の全パターンのうちComponents v2になっている割合です。

## 集計

| 分野 | テキスト | Embed | v2 | 合計 | 交換率 |
|---|---:|---:|---:|---:|---:|
| VCへの接続・切断 | 1 | 0 | 8 | 9 | 88.9% |
| 読み上げの操作（`/clear`・`/skip`・`/t`・`!t`） | 3 | 7 | 0 | 10 | 0.0% |
| 読み辞書（`/dict`） | 2 | 15 | 0 | 17 | 0.0% |
| 音声辞書（`/sounddict`） | 0 | 9 | 0 | 9 | 0.0% |
| サーバー設定（`/setting`） | 0 | 24 | 0 | 24 | 0.0% |
| ユーザー設定（`/user-reading`） | 0 | 4 | 0 | 4 | 0.0% |
| 一般（`/version`） | 0 | 1 | 0 | 1 | 0.0% |
| 共通のエラー応答 | 0 | 2 | 0 | 2 | 0.0% |
| 再起動・再接続 | 0 | 2 | 0 | 2 | 0.0% |
| サーバー退出 | 1 | 0 | 0 | 1 | 0.0% |
| 運営者: お知らせ（`/announce`） | 0 | 0 | 16 | 16 | 100.0% |
| 運営者: 生成の記録（`/synthesis-log`） | 0 | 3 | 0 | 3 | 0.0% |
| 運営者: 更新の知らせ・更新ボタン | 3 | 11 | 0 | 14 | 0.0% |
| 運営者: エラー通知 | 0 | 1 | 0 | 1 | 0.0% |
| **合計** | **10** | **79** | **24** | **113** | **21.2%** |

テキストを移し替えの対象から外し、Embedとv2だけで数えると、交換率は 24 / 103 = **23.3%** です。

---

## テキスト（10）

### VCへの接続・切断（1）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「あなたが接続したテキストチャンネルに権限がないため、自動接続が失敗しました」 | AutoJoinで、読み上げチャンネルに権限がない | 本人へDM | [connection_service.py:370](../services/connection_service.py#L370) |

### 読み上げの操作（3）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| WAVファイルだけ | `/tsumugi-tts`・`/t`の音声ファイル | コマンドの応答 | [playback_cog.py:116](../cogs/playback_cog.py#L116) |
| WAVファイルだけ | `!t`の音声ファイル（返信先が消えたときは返信にせず送る） | 返信 | [message_service.py:124](../services/message_service.py#L124)・[135](../services/message_service.py#L135) |
| 「音声ファイルが大きすぎて送れませんでした。文章を短くしてください。」 | `!t`のファイルが大きすぎる（413） | チャンネル | [message_service.py:130](../services/message_service.py#L130) |

### 読み辞書（2）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 読み込まなかった行の一覧（長いときはtxt添付） | `/dict import`で読み込まなかった行がある | 実行者へDM | [word_dict.py:262](../word_dict.py#L262) |
| 同上 | DMを送れなかった | 本人だけに見える応答（followup） | [word_dict.py:573](../word_dict.py#L573) |

### サーバー退出（1）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「サーバー「…」の辞書データをお送りします。」＋JSON添付 | Botがサーバーから外された | サーバーの持ち主へDM | [lifecycle_cog.py:115](../cogs/lifecycle_cog.py#L115) |

### 運営者: 更新の知らせ・更新ボタン（3）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「今は更新を受け付けられません」 | 更新の見張りが動いていないときにボタンが押された | 本人だけ | [update_watch_service.py:144](../services/update_watch_service.py#L144) |
| 「この操作は、Botを運営している人だけができます」 | 運営者以外がボタンを押した | 本人だけ | [update_watch_service.py:413](../services/update_watch_service.py#L413) |
| 「新しい版 … があります」／「… への更新は、もう受け付けていません」 | 古いお知らせのボタンが押された | 本人だけ | [update_watch_service.py:428](../services/update_watch_service.py#L428) |

---

## Embed（79）

### 読み上げの操作（7）

| 見出し | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 削除中 | `/clear`・`/c`の途中 | コマンドの応答 | [playback_cog.py:31](../cogs/playback_cog.py#L31) |
| キュークリア完了 | `/clear`・`/c`が終わった | コマンドの応答 | [playback_cog.py:38](../cogs/playback_cog.py#L38) |
| スキップ | `/skip`・`/s`でスキップした | 本人だけ | [playback_cog.py:56](../cogs/playback_cog.py#L56) |
| スキップ | `/skip`・`/s`で再生中のものがない | 本人だけ | [playback_cog.py:62](../cogs/playback_cog.py#L62) |
| 音声ファイル | `/t`でBotがVCにいない | 本人だけ | [playback_cog.py:78](../cogs/playback_cog.py#L78) |
| 音声ファイル | `/t`の文章が空 | 本人だけ | [playback_cog.py:89](../cogs/playback_cog.py#L89) |
| 音声ファイル | `/t`で音声を作れなかった | コマンドの応答 | [playback_cog.py:108](../cogs/playback_cog.py#L108) |

### 読み辞書（15）

| 見出し | 場面 | 場所 |
|---|---|---|
| 読みが長すぎるため追加できませんでした | `/dict add`の読みが50文字を超える | [word_dict.py:302](../word_dict.py#L302) |
| 辞書への追加（上書き）が成功しました | `/dict add` | [word_dict.py:312](../word_dict.py#L312) |
| 辞書にその単語は見つかりませんでした | `/dict del` | [word_dict.py:335](../word_dict.py#L335) |
| 辞書からの削除が成功しました | `/dict del` | [word_dict.py:345](../word_dict.py#L345) |
| 辞書一覧（登録なし） | `/dict view` | [word_dict.py:382](../word_dict.py#L382) |
| 見つかりませんでした | `/dict view`の検索 | [word_dict.py:392](../word_dict.py#L392) |
| 辞書一覧（ページ送りボタン付き） | `/dict view` | [word_dict.py:410](../word_dict.py#L410)・[dict_view.py:39](../dict_view.py#L39) |
| 共通辞書（登録なし） | `/dict view-common` | [word_dict.py:440](../word_dict.py#L440) |
| 見つかりませんでした | `/dict view-common`の検索 | [word_dict.py:450](../word_dict.py#L450) |
| 共通辞書の一覧（ページ送りボタン付き） | `/dict view-common` | [word_dict.py:469](../word_dict.py#L469) |
| 辞書の書き出し（登録なし） | `/dict export` | [word_dict.py:496](../word_dict.py#L496) |
| 辞書を書き出しました（.dict添付） | `/dict export` | [word_dict.py:503](../word_dict.py#L503) |
| 辞書を読み込めませんでした | `/dict import`がファイル全体を断った | [word_dict.py:538](../word_dict.py#L538) |
| 辞書を読み込みました | `/dict import` | [word_dict.py:547](../word_dict.py#L547) |
| 権限エラー | `/dict`をサーバー管理権限なしで実行 | [word_dict.py:584](../word_dict.py#L584) |

### 音声辞書（9）

| 見出し | 場面 | 場所 |
|---|---|---|
| サウンドが見つかりませんでした | `/sounddict add` | [sound_dict.py:120](../sound_dict.py#L120) |
| 辞書への追加（上書き）が成功しました | `/sounddict add`で読みも登録 | [sound_dict.py:140](../sound_dict.py#L140) |
| 音声辞書への追加（上書き）が成功しました | `/sounddict add`で音声だけ登録 | [sound_dict.py:149](../sound_dict.py#L149) |
| 音声辞書にその単語は見つかりませんでした | `/sounddict del` | [sound_dict.py:192](../sound_dict.py#L192) |
| 音声辞書からの削除が成功しました | `/sounddict del` | [sound_dict.py:202](../sound_dict.py#L202) |
| 音声辞書一覧（登録なし） | `/sounddict view` | [sound_dict.py:241](../sound_dict.py#L241) |
| 見つかりませんでした | `/sounddict view`の検索 | [sound_dict.py:265](../sound_dict.py#L265) |
| 音声辞書一覧（ページ送りボタン付き） | `/sounddict view` | [sound_dict.py:282](../sound_dict.py#L282) |
| 権限エラー | `/sounddict`をサーバー管理権限なしで実行 | [sound_dict.py:299](../sound_dict.py#L299) |

### サーバー設定（24）

| 見出し | 場面 | 場所 |
|---|---|---|
| サーバー設定 | `/setting view` | [setting.py:108](../setting.py#L108) |
| 設定失敗 | `text-target`で表示・送信権限がない | [setting.py:169](../setting.py#L169) |
| 設定完了 | `text-target` | [setting.py:179](../setting.py#L179) |
| 設定リセット | `text-target-reset` | [setting.py:204](../setting.py#L204) |
| 設定失敗 | `voice-target`でVCに入っていない | [setting.py:233](../setting.py#L233) |
| 設定失敗 | `voice-target`で接続権限がない | [setting.py:244](../setting.py#L244) |
| 設定完了 | `voice-target` | [setting.py:253](../setting.py#L253) |
| 設定リセット | `voice-target-reset` | [setting.py:278](../setting.py#L278) |
| 設定完了 | `speaker`をBotのデフォルトにした | [setting.py:302](../setting.py#L302) |
| 設定失敗 | `speaker`の話者が見つからない | [setting.py:312](../setting.py#L312) |
| 設定完了 | `speaker` | [setting.py:321](../setting.py#L321) |
| 設定失敗 | `volume`の範囲外 | [setting.py:359](../setting.py#L359) |
| 設定完了 | `volume` | [setting.py:367](../setting.py#L367) |
| 設定失敗 | `speed`の範囲外 | [setting.py:395](../setting.py#L395) |
| 設定完了 | `speed` | [setting.py:403](../setting.py#L403) |
| 設定失敗 | `spaced-speed`の範囲外 | [setting.py:431](../setting.py#L431) |
| 設定完了 | `spaced-speed` | [setting.py:439](../setting.py#L439) |
| 設定完了 | `spaced-speed-reset` | [setting.py:466](../setting.py#L466) |
| 設定失敗 | `max-char`の範囲外 | [setting.py:504](../setting.py#L504) |
| 設定完了 | `max-char` | [setting.py:512](../setting.py#L512) |
| 設定失敗 | `auto-join`でVC・チャンネルを決められない | [setting.py:551](../setting.py#L551) |
| 設定完了 | `auto-join` | [setting.py:570](../setting.py#L570) |
| 設定完了 | `access-notice` | [setting.py:597](../setting.py#L597) |
| 権限エラー | `/setting`をサーバー管理権限なしで実行 | [setting.py:616](../setting.py#L616) |

### ユーザー設定（4）

| 見出し | 場面 | 場所 |
|---|---|---|
| 設定失敗 | `/user-reading`の読みが不正 | [user_cog.py:35](../cogs/user_cog.py#L35) |
| 設定完了 | `/user-reading` | [user_cog.py:43](../cogs/user_cog.py#L43) |
| リセット完了 | `/user-reading-reset` | [user_cog.py:84](../cogs/user_cog.py#L84) |
| リセット不要 | `/user-reading-reset`で未設定 | [user_cog.py:90](../cogs/user_cog.py#L90) |

### 一般（1）

| 見出し | 場面 | 場所 |
|---|---|---|
| バージョン情報 | `/version` | [general_cog.py:30](../cogs/general_cog.py#L30) |

### 共通のエラー応答（2）

| 見出し | 場面 | 場所 |
|---|---|---|
| システムエラー | コマンドで設定の読み書き（OSの操作）に失敗した | [error_handler.py:16](../presentation/error_handler.py#L16) |
| 内部エラーが発生しました | コマンドで想定外の例外が起きた | [error_handler.py:36](../presentation/error_handler.py#L36) |

### 再起動・再接続（2）

| 見出し | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 再起動します | 更新・再起動で止まる前（文面は理由で2通り） | 読み上げチャンネル | [reconnect_service.py:42](../services/reconnect_service.py#L42) |
| 再接続しました | 再起動のあと、同じVCへつなぎ直した | 読み上げチャンネル | [reconnect_service.py:46](../services/reconnect_service.py#L46) |

### 運営者: 生成の記録（3）

| 見出し | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 音声生成の記録 | 運営者以外が実行 | 本人だけ | [operator_cog.py:175](../cogs/operator_cog.py#L175) |
| 音声生成の記録（集計＋txt添付） | 記録がある | DM | [operator_cog.py:150](../cogs/operator_cog.py#L150)・[187](../cogs/operator_cog.py#L187) |
| 音声生成の記録（記録はまだありません） | 記録がない | DM | [operator_cog.py:149](../cogs/operator_cog.py#L149) |

### 運営者: 更新の知らせ・更新ボタン（11）

すべて運営者へのDM、またはそのDMの書き換えです。

| 見出し | 場面 | 場所 |
|---|---|---|
| 更新しました | 更新の結果（`updated`） | [update_watch_service.py:62](../services/update_watch_service.py#L62) |
| 入れ直しました | 更新の結果（`repaired`） | [update_watch_service.py:64](../services/update_watch_service.py#L64) |
| 更新できませんでした | 更新の結果（`rolled_back`） | [update_watch_service.py:76](../services/update_watch_service.py#L76) |
| 1つ前の版に戻しました | 更新の結果（`rollback`） | [update_watch_service.py:78](../services/update_watch_service.py#L78) |
| 更新の結果 | 更新の結果（そのほか） | [update_watch_service.py:83](../services/update_watch_service.py#L83) |
| 自動更新を止めています | 自動更新が人の手を要る（`unfinished`） | [update_watch_service.py:87](../services/update_watch_service.py#L87) |
| 新しい版があります（更新ボタン付き） | 新しいバージョンを見つけた | [update_watch_service.py:99](../services/update_watch_service.py#L99)・[321](../services/update_watch_service.py#L321) |
| 更新を開始しました | 更新が始まり、ボタンのDMを書き換える | [update_watch_service.py:103](../services/update_watch_service.py#L103)・[375](../services/update_watch_service.py#L375) |
| 更新は済んでいます | 今と同じバージョンのボタンが押された | [update_watch_service.py:418](../services/update_watch_service.py#L418) |
| 更新を受け付けました | 「今すぐ更新する」 | [update_watch_service.py:434](../services/update_watch_service.py#L434) |
| VCが空いたら更新します（更新ボタン付き） | 「VCが空いたら更新する」 | [update_watch_service.py:445](../services/update_watch_service.py#L445) |

### 運営者: エラー通知（1）

| 見出し | 場面 | 場所 |
|---|---|---|
| エラーが発生しました | エラーを運営者全員へDM（長いときは分けて送る） | [error_notification_service.py:121](../services/error_notification_service.py#L121) |

---

## Components v2（24）

### VCへの接続・切断（8）

| 見出し | 枠の色 | 場面 | 場所 |
|---|---|---|---|
| 接続しました（お知らせ付き） | 緑 | `/join`・`@Bot`単体メンション | [connection_service.py:243](../services/connection_service.py#L243)・[453](../services/connection_service.py#L453) |
| 自動で接続しました（お知らせ付き） | 青 | AutoJoin | [connection_service.py:405](../services/connection_service.py#L405) |
| 切断完了 | 緑 | `/leave`・メンション | [connection_service.py:200](../services/connection_service.py#L200)・[469](../services/connection_service.py#L469) |
| 切断失敗 | 赤 | `/leave`・メンションで、実行者がVCにいない | [connection_service.py:207](../services/connection_service.py#L207)・[476](../services/connection_service.py#L476) |
| 接続失敗 | 赤 | 実行者がVCにいない | [connection_service.py:216](../services/connection_service.py#L216)・[427](../services/connection_service.py#L427) |
| 接続失敗 | 赤 | Discordとの音声接続に失敗した | [connection_service.py:252](../services/connection_service.py#L252) |
| 権限エラー | 赤 | `/join`・メンション・AutoJoinで権限が足りない | [connection_service.py:231](../services/connection_service.py#L231)・[374](../services/connection_service.py#L374)・[439](../services/connection_service.py#L439) |
| 自動退出 | 青 | VCに誰もいなくなった | [connection_service.py:326](../services/connection_service.py#L326) |

### 運営者: お知らせ（16）

| 見出し | 場面 | 場所 |
|---|---|---|
| お知らせ（運営者だけが使えます） | 運営者以外がコマンド・ボタンを使った | [announcement_cog.py:366](../cogs/announcement_cog.py#L366) |
| お知らせ（入力の誤り＋入力し直すボタン） | Modalの入力に誤りがある | [announcement_cog.py:456](../cogs/announcement_cog.py#L456) |
| お知らせ（画像を読み込めない＋入力し直すボタン） | 画像のダウンロードに失敗した | [announcement_cog.py:460](../cogs/announcement_cog.py#L460) |
| お知らせ（引数の誤り・IDが見つからない） | `add`・`edit`・`del`・`preview`の引数 | [announcement_cog.py:373](../cogs/announcement_cog.py#L373) |
| お知らせ（内部エラー） | 想定外の例外 | [announcement_cog.py:386](../cogs/announcement_cog.py#L386) |
| 確認（プレビュー＋送信する・内容を変更する・破棄する） | Modalを送信した | [announcement_cog.py:469](../cogs/announcement_cog.py#L469) |
| ID N のお知らせを追加（更新）しました | 「送信する」 | [announcement_cog.py:293](../cogs/announcement_cog.py#L293) |
| お知らせを追加（更新）できませんでした | 保存するときの再確認で断った | [announcement_cog.py:282](../cogs/announcement_cog.py#L282) |
| お知らせを破棄しました | 「破棄する」 | [announcement_cog.py:316](../cogs/announcement_cog.py#L316) |
| 内容を変更しました | 「内容を変更する」で新しい確認を出した | [announcement_cog.py:326](../cogs/announcement_cog.py#L326) |
| 確認の期限が切れました | 15分押されなかった | [announcement_cog.py:337](../cogs/announcement_cog.py#L337) |
| お知らせ（この確認はもう使えません） | 終わった確認のボタンが押された | [announcement_cog.py:257](../cogs/announcement_cog.py#L257) |
| お知らせの一覧（お知らせはありません） | `/announce list`で0件 | [announcement_cog.py:499](../cogs/announcement_cog.py#L499) |
| お知らせの一覧 | `/announce list` | [announcement_cog.py:500](../cogs/announcement_cog.py#L500) |
| お知らせを削除しました | `/announce del` | [announcement_cog.py:638](../cogs/announcement_cog.py#L638) |
| プレビュー | `/announce preview` | [announcement_cog.py:676](../cogs/announcement_cog.py#L676) |

---

## 更新のしかた

メッセージを足したり、形式を移し替えたりしたら、この表と集計を直してください。送信箇所は、次の検索で洗い出せます（`tests/`と`.tsumugium/`は除く）。

```bash
rg -n "make_embed\(|make_layout\(|\.send\(|send_message\(|edit_message\(|edit_original_response\(|\.reply\(" --glob '!tests/**' --glob '!.tsumugium/**'
```
