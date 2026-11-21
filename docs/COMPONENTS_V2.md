# Components v2 表示対応表

Botが出すメッセージを、表示の形式（テキスト・Embed・Components v2）ごとに分け、分野別に並べた表です。Components v2への移し替えがどこまで進んだかを確かめるために使います。移し替えの方針は[`AGENTS.md`](../AGENTS.md)の「Components v2への移行」を参照してください。

新しいコマンドとメッセージは、基本的にComponents v2（`make_layout()`）で作ります。Embedで作るのは、v2では表せないなど、理由があるときだけにしてください。

調べた時点: `feat/synthesis_log-auto-send`（`/synthesis log`をv2へ移し、生成の記録の自動送信を足したあと）

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
| 運営者: ストリーミング合成の設定（`/synthesis view`・`set`・`reset`） | 0 | 0 | 5 | 5 | 100.0% |
| 運営者: 生成の記録（`/synthesis log`・自動送信） | 2 | 0 | 5 | 7 | 71.4% |
| 運営者: 更新の知らせ・更新ボタン・`/update` | 3 | 0 | 22 | 25 | 88.0% |
| 運営者: エラー通知 | 0 | 1 | 0 | 1 | 0.0% |
| **合計** | **12** | **65** | **56** | **133** | **42.1%** |

テキストを移し替えの対象から外し、Embedとv2だけで数えると、交換率は 56 / 121 = **46.3%** です。

---

## テキスト（12）

### VCへの接続・切断（1）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「あなたが接続したテキストチャンネルに権限がないため、自動接続が失敗しました」 | AutoJoinで、読み上げチャンネルに権限がない | 本人へDM | [connection_service.py:370](../services/connection_service.py#L370) |

### 読み上げの操作（3）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| WAVファイルだけ | `/tsumugi-tts`・`/t`の音声ファイル | コマンドの応答 | [playback_cog.py:86](../cogs/playback_cog.py#L86) |
| WAVファイルだけ | `!t`の音声ファイル（返信先が消えたときは返信にせず送る） | 返信 | [message_service.py:124](../services/message_service.py#L124)・[135](../services/message_service.py#L135) |
| 「音声ファイルが大きすぎて送れませんでした。文章を短くしてください。」 | `!t`のファイルが大きすぎる（413） | チャンネル | [message_service.py:130](../services/message_service.py#L130) |

### 読み辞書（2）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 読み込まなかった行の一覧（長いときはtxt添付） | `/dict import`で読み込まなかった行がある | 実行者へDM | [word_dict.py:262](../word_dict.py#L262) |
| 同上 | DMを送れなかった | 本人だけに見える応答（followup） | [word_dict.py:546](../word_dict.py#L546) |

### サーバー退出（1）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「サーバー「…」の辞書データをお送りします。」＋JSON添付 | Botがサーバーから外された | サーバーの持ち主へDM | [lifecycle_cog.py:115](../cogs/lifecycle_cog.py#L115) |

### 運営者: 更新の知らせ・更新ボタン（3）

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 「今は更新を受け付けられません」 | 更新の見張りが動いていないときにボタンが押された | 本人だけ | [update_watch_service.py:163](../services/update_watch_service.py#L163) |
| 「この操作は、Botを運営している人だけができます」 | 運営者以外がボタンを押した | 本人だけ | [update_watch_service.py:548](../services/update_watch_service.py#L548) |
| 「新しいバージョン … があります」／「… への更新は、もう受け付けていません」 | 古いお知らせのボタンが押された | 本人だけ | [update_watch_service.py:562](../services/update_watch_service.py#L562) |

### 運営者: 生成の記録（2）

Components v2 の中ではtxtの中身を表示できないので、集計のv2のメッセージの直後に、一覧のファイルだけを別に送ります。

| 表示 | 場面 | 送り先 | 場所 |
|---|---|---|---|
| `synthesis-log-YYYYMMDD-HHMMSS.txt`だけ | `/synthesis log`で記録がある | コマンドの応答（followup） | [operator_cog.py:244](../cogs/operator_cog.py#L244) |
| `synthesis-log-YYYYMMDD-HHMMSS.txt`だけ | 自動送信（500件たまった・止める直前） | 運営者全員へDM | [generation_log_report_service.py:113](../services/generation_log_report_service.py#L113) |

---

## Embed（65）

### 読み上げの操作（7）

| 見出し | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 削除中 | `/clear`・`/c`の途中 | コマンドの応答 | [playback_cog.py:31](../cogs/playback_cog.py#L31) |
| キュークリア完了 | `/clear`・`/c`が終わった | コマンドの応答 | [playback_cog.py:34](../cogs/playback_cog.py#L34) |
| スキップ | `/skip`・`/s`でスキップした | 本人だけ | [playback_cog.py:47](../cogs/playback_cog.py#L47) |
| スキップ | `/skip`・`/s`で再生中のものがない | 本人だけ | [playback_cog.py:49](../cogs/playback_cog.py#L49) |
| 音声ファイル | `/t`でBotがVCにいない | 本人だけ | [playback_cog.py:65](../cogs/playback_cog.py#L65) |
| 音声ファイル | `/t`の文章が空 | 本人だけ | [playback_cog.py:70](../cogs/playback_cog.py#L70) |
| 音声ファイル | `/t`で音声を作れなかった | コマンドの応答 | [playback_cog.py:83](../cogs/playback_cog.py#L83) |

### 読み辞書（15）

| 見出し | 場面 | 場所 |
|---|---|---|
| 読みが長すぎるため追加できませんでした | `/dict add`の読みが50文字を超える | [word_dict.py:302](../word_dict.py#L302) |
| 辞書への追加（上書き）が成功しました | `/dict add` | [word_dict.py:311](../word_dict.py#L311) |
| 辞書にその単語は見つかりませんでした | `/dict del` | [word_dict.py:332](../word_dict.py#L332) |
| 辞書からの削除が成功しました | `/dict del` | [word_dict.py:341](../word_dict.py#L341) |
| 辞書一覧（登録なし） | `/dict view` | [word_dict.py:374](../word_dict.py#L374) |
| 見つかりませんでした | `/dict view`の検索 | [word_dict.py:384](../word_dict.py#L384) |
| 辞書一覧（ページ送りボタン付き） | `/dict view` | [word_dict.py:401](../word_dict.py#L401)・[dict_view.py:39](../dict_view.py#L39) |
| 共通辞書（登録なし） | `/dict view-common` | [word_dict.py:431](../word_dict.py#L431) |
| 見つかりませんでした | `/dict view-common`の検索 | [word_dict.py:437](../word_dict.py#L437) |
| 共通辞書の一覧（ページ送りボタン付き） | `/dict view-common` | [word_dict.py:455](../word_dict.py#L455) |
| 辞書の書き出し（登録なし） | `/dict export` | [word_dict.py:482](../word_dict.py#L482) |
| 辞書を書き出しました（.dict添付） | `/dict export` | [word_dict.py:489](../word_dict.py#L489) |
| 辞書を読み込めませんでした | `/dict import`がファイル全体を断った | [word_dict.py:520](../word_dict.py#L520) |
| 辞書を読み込みました | `/dict import` | [word_dict.py:524](../word_dict.py#L524) |
| 権限エラー | `/dict`をサーバー管理権限なしで実行 | [word_dict.py:557](../word_dict.py#L557) |

### 音声辞書（9）

| 見出し | 場面 | 場所 |
|---|---|---|
| サウンドが見つかりませんでした | `/sounddict add` | [sound_dict.py:120](../sound_dict.py#L120) |
| 辞書への追加（上書き）が成功しました | `/sounddict add`で読みも登録 | [sound_dict.py:139](../sound_dict.py#L139) |
| 音声辞書への追加（上書き）が成功しました | `/sounddict add`で音声だけ登録 | [sound_dict.py:148](../sound_dict.py#L148) |
| 音声辞書にその単語は見つかりませんでした | `/sounddict del` | [sound_dict.py:191](../sound_dict.py#L191) |
| 音声辞書からの削除が成功しました | `/sounddict del` | [sound_dict.py:200](../sound_dict.py#L200) |
| 音声辞書一覧（登録なし） | `/sounddict view` | [sound_dict.py:235](../sound_dict.py#L235) |
| 見つかりませんでした | `/sounddict view`の検索 | [sound_dict.py:259](../sound_dict.py#L259) |
| 音声辞書一覧（ページ送りボタン付き） | `/sounddict view` | [sound_dict.py:275](../sound_dict.py#L275) |
| 権限エラー | `/sounddict`をサーバー管理権限なしで実行 | [sound_dict.py:292](../sound_dict.py#L292) |

### サーバー設定（24）

| 見出し | 場面 | 場所 |
|---|---|---|
| サーバー設定 | `/setting view` | [setting.py:108](../setting.py#L108) |
| 設定失敗 | `text-target`で表示・送信権限がない | [setting.py:169](../setting.py#L169) |
| 設定完了 | `text-target` | [setting.py:174](../setting.py#L174) |
| 設定リセット | `text-target-reset` | [setting.py:198](../setting.py#L198) |
| 設定失敗 | `voice-target`でVCに入っていない | [setting.py:222](../setting.py#L222) |
| 設定失敗 | `voice-target`で接続権限がない | [setting.py:232](../setting.py#L232) |
| 設定完了 | `voice-target` | [setting.py:236](../setting.py#L236) |
| 設定リセット | `voice-target-reset` | [setting.py:260](../setting.py#L260) |
| 設定完了 | `speaker`をBotのデフォルトにした | [setting.py:279](../setting.py#L279) |
| 設定失敗 | `speaker`の話者が見つからない | [setting.py:284](../setting.py#L284) |
| 設定完了 | `speaker` | [setting.py:288](../setting.py#L288) |
| 設定失敗 | `volume`の範囲外 | [setting.py:325](../setting.py#L325) |
| 設定完了 | `volume` | [setting.py:328](../setting.py#L328) |
| 設定失敗 | `speed`の範囲外 | [setting.py:351](../setting.py#L351) |
| 設定完了 | `speed` | [setting.py:354](../setting.py#L354) |
| 設定失敗 | `spaced-speed`の範囲外 | [setting.py:377](../setting.py#L377) |
| 設定完了 | `spaced-speed` | [setting.py:380](../setting.py#L380) |
| 設定完了 | `spaced-speed-reset` | [setting.py:402](../setting.py#L402) |
| 設定失敗 | `max-char`の範囲外 | [setting.py:435](../setting.py#L435) |
| 設定完了 | `max-char` | [setting.py:442](../setting.py#L442) |
| 設定失敗 | `auto-join`でVC・チャンネルを決められない | [setting.py:480](../setting.py#L480) |
| 設定完了 | `auto-join` | [setting.py:498](../setting.py#L498) |
| 設定完了 | `access-notice` | [setting.py:520](../setting.py#L520) |
| 権限エラー | `/setting`をサーバー管理権限なしで実行 | [setting.py:534](../setting.py#L534) |

### ユーザー設定（4）

| 見出し | 場面 | 場所 |
|---|---|---|
| 設定失敗 | `/user-reading`の読みが不正 | [user_cog.py:35](../cogs/user_cog.py#L35) |
| 設定完了 | `/user-reading` | [user_cog.py:38](../cogs/user_cog.py#L38) |
| リセット完了 | `/user-reading-reset` | [user_cog.py:74](../cogs/user_cog.py#L74) |
| リセット不要 | `/user-reading-reset`で未設定 | [user_cog.py:76](../cogs/user_cog.py#L76) |

### 一般（1）

| 見出し | 場面 | 場所 |
|---|---|---|
| バージョン情報 | `/version` | [general_cog.py:30](../cogs/general_cog.py#L30) |

### 共通のエラー応答（2）

| 見出し | 場面 | 場所 |
|---|---|---|
| システムエラー | コマンドで設定の読み書き（OSの操作）に失敗した | [error_handler.py:16](../presentation/error_handler.py#L16) |
| 内部エラーが発生しました | コマンドで想定外の例外が起きた | [error_handler.py:31](../presentation/error_handler.py#L31) |

### 再起動・再接続（2）

| 見出し | 場面 | 送り先 | 場所 |
|---|---|---|---|
| 再起動します | 更新・再起動で止まる前（文面は理由で2通り） | 読み上げチャンネル | [reconnect_service.py:42](../services/reconnect_service.py#L42) |
| 再接続しました | 再起動のあと、同じVCへつなぎ直した | 読み上げチャンネル | [reconnect_service.py:46](../services/reconnect_service.py#L46) |

### 運営者: エラー通知（1）

| 見出し | 場面 | 場所 |
|---|---|---|
| エラーが発生しました | エラーを運営者全員へDM（長いときは分けて送る） | [error_notification_service.py:121](../services/error_notification_service.py#L121) |

---

## Components v2（56）

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

### 運営者: 更新の知らせ・更新ボタン・`/update`（22）

`/update`の応答のほかは、すべて運営者へのDM、またはそのDMの書き換えです。更新ボタンは枠の外（下）に置きます。Embedで送った前の版のDMを書き換えるときは、`embed=None`を渡してv2に置き換えます。

| 見出し | 枠の色 | 場面 | 場所 |
|---|---|---|---|
| 更新しました | 緑 | 更新の結果（`updated`） | [update_watch_service.py:77](../services/update_watch_service.py#L77) |
| 入れ直しました | 緑 | 更新の結果（`repaired`） | [update_watch_service.py:79](../services/update_watch_service.py#L79) |
| 更新できませんでした | 赤 | 更新の結果（`rolled_back`） | [update_watch_service.py:91](../services/update_watch_service.py#L91) |
| 1つ前の版に戻しました | 黄 | 更新の結果（`rollback`） | [update_watch_service.py:93](../services/update_watch_service.py#L93) |
| 更新の結果 | 青 | 更新の結果（そのほか） | [update_watch_service.py:98](../services/update_watch_service.py#L98) |
| 自動更新を止めています | 黄 | 自動更新が人の手を要る（`unfinished`） | [update_watch_service.py:102](../services/update_watch_service.py#L102) |
| 新しいバージョンがあります（更新ボタン付き） | 青 | 新しいバージョンを見つけた・`/update`で見つけた | [update_watch_service.py:115](../services/update_watch_service.py#L115)・[360](../services/update_watch_service.py#L360)・[363](../services/update_watch_service.py#L363) |
| 更新を開始しました | 緑 | 更新が始まり、ボタンのDMを書き換える | [update_watch_service.py:121](../services/update_watch_service.py#L121)・[512](../services/update_watch_service.py#L512) |
| 更新は済んでいます | 青 | 今と同じバージョンのボタンが押された | [update_watch_service.py:553](../services/update_watch_service.py#L553) |
| 更新を受け付けました | 緑 | 「今すぐ更新する」 | [update_watch_service.py:568](../services/update_watch_service.py#L568) |
| VCが空いたら更新します（更新ボタン付き） | 青 | 「VCが空いたら更新する」 | [update_watch_service.py:577](../services/update_watch_service.py#L577) |
| Bot運営者専用コマンドです | 赤 | 運営者以外が`/update`・`/synthesis view`・`set`・`reset`を実行（本人だけ） | [operator_cog.py:100](../cogs/operator_cog.py#L100) |
| 更新できません | 赤 | 更新の見張りがない（`/update`） | [operator_cog.py:270](../cogs/operator_cog.py#L270) |
| 新しいバージョンの確認（手動で導入されている） | 赤 | `UPDATE_STATE_DIR`が空欄 | [update_watch_service.py:385](../services/update_watch_service.py#L385) |
| 新しいバージョンの確認（更新中です） | 黄 | 更新中 | [update_watch_service.py:391](../services/update_watch_service.py#L391) |
| 新しいバージョンの確認（ほかで更新を問い合わせています） | 黄 | 扱われていない依頼がある | [update_watch_service.py:393](../services/update_watch_service.py#L393) |
| 新しいバージョンの確認（連続してのリクエストはできません） | 黄 | 前の確認から60秒たっていない | [update_watch_service.py:401](../services/update_watch_service.py#L401) |
| 新しいバージョンの確認（時間内に応答がありませんでした） | 黄 | 60秒待っても結果が届かない | [update_watch_service.py:417](../services/update_watch_service.py#L417) |
| 新しいバージョンの確認（更新はありません） | 青 | 新しいバージョンがない | [update_watch_service.py:425](../services/update_watch_service.py#L425) |
| 新しいバージョンの確認（新しいバージョン … があります） | 緑 | 新しいバージョンがあり、ボタン付きのDMを送った | [update_watch_service.py:429](../services/update_watch_service.py#L429) |
| 新しいバージョンの確認（… が見つかりましたが、Discordからは更新できません） | 黄 | 新しいバージョンがあるが、DMを送れなかった | [update_watch_service.py:434](../services/update_watch_service.py#L434) |
| 新しいバージョンの確認（インストーラーの文） | 赤 | 確かめられなかった・前回の更新が途中で止まっている | [update_watch_service.py:440](../services/update_watch_service.py#L440) |

### 運営者: ストリーミング合成の設定（5）

運営者以外への断り（「Bot運営者専用コマンドです」）は、`/update`と同じメッセージなので「運営者: 更新の知らせ・更新ボタン・`/update`」で数えています。

| 見出し | 色 | 場面 | 場所 |
|---|---|---|---|
| ストリーミング合成の設定 | 青 | `/synthesis view` | [operator_cog.py:138](../cogs/operator_cog.py#L138) |
| ストリーミング合成の設定（変える項目を指定してください） | 赤 | `/synthesis set`で何も指定しなかった | [operator_cog.py:171](../cogs/operator_cog.py#L171) |
| ストリーミング合成の設定（今は設定を変えることができません） | 赤 | 設定のサービスがない | [operator_cog.py:111](../cogs/operator_cog.py#L111) |
| ストリーミング合成の設定を変えました | 緑 | `/synthesis set` | [operator_cog.py:183](../cogs/operator_cog.py#L183) |
| ストリーミング合成の設定を初期値に戻しました | 緑 | `/synthesis reset` | [operator_cog.py:207](../cogs/operator_cog.py#L207) |

### 運営者: 生成の記録（5）

集計（`TextDisplay`）だけを載せる。v2の中ではtxtの中身を表示できないので、一覧のtxtファイルは直後にファイルだけのメッセージで別に送る（下の「テキスト」に数える）。`/synthesis log`と自動送信は同じ`make_log_layout()`で作る。

| 見出し | 色 | 場面 | 場所 |
|---|---|---|---|
| 音声生成の記録（このコマンドはBotの運営者だけが使えます） | 赤 | 運営者以外が`/synthesis log`を実行（本人だけ） | [operator_cog.py:231](../cogs/operator_cog.py#L231) |
| 音声生成の記録（集計） | 青 | `/synthesis log`で記録がある | [operator_cog.py:91](../cogs/operator_cog.py#L91)・[generation_log_format.py:189](../presentation/generation_log_format.py#L189) |
| 音声生成の記録（記録はまだありません） | 青 | `/synthesis log`で記録がない | [operator_cog.py:91](../cogs/operator_cog.py#L91) |
| 音声生成の記録（記録が500件たまったため送ります） | 青 | まだ送っていない記録が500件たまった（運営者全員へDM） | [generation_log_report_service.py:45](../services/generation_log_report_service.py#L45) |
| 音声生成の記録（…の前に、まだ送っていない記録を送ります） | 青 | 正常な再起動・停止・更新の直前（文面は理由で4通り、運営者全員へDM） | [generation_log_report_service.py:30](../services/generation_log_report_service.py#L30) |

### 運営者: お知らせ（16）

| 見出し | 場面 | 場所 |
|---|---|---|
| お知らせ（運営者だけが使えます） | 運営者以外がコマンド・ボタンを使った | [announcement_cog.py:352](../cogs/announcement_cog.py#L352) |
| お知らせ（入力の誤り＋入力し直すボタン） | Modalの入力に誤りがある | [announcement_cog.py:442](../cogs/announcement_cog.py#L442) |
| お知らせ（画像を読み込めない＋入力し直すボタン） | 画像のダウンロードに失敗した | [announcement_cog.py:446](../cogs/announcement_cog.py#L446) |
| お知らせ（引数の誤り・IDが見つからない） | `add`・`edit`・`del`・`preview`の引数 | [announcement_cog.py:359](../cogs/announcement_cog.py#L359) |
| お知らせ（内部エラー） | 想定外の例外 | [announcement_cog.py:372](../cogs/announcement_cog.py#L372) |
| 確認（プレビュー＋送信する・内容を変更する・破棄する） | Modalを送信した | [announcement_cog.py:455](../cogs/announcement_cog.py#L455) |
| ID N のお知らせを追加（更新）しました | 「送信する」 | [announcement_cog.py:287](../cogs/announcement_cog.py#L287) |
| お知らせを追加（更新）できませんでした | 保存するときの再確認で断った | [announcement_cog.py:280](../cogs/announcement_cog.py#L280) |
| お知らせを破棄しました | 「破棄する」 | [announcement_cog.py:306](../cogs/announcement_cog.py#L306) |
| 内容を変更しました | 「内容を変更する」で新しい確認を出した | [announcement_cog.py:314](../cogs/announcement_cog.py#L314) |
| 確認の期限が切れました | 15分押されなかった | [announcement_cog.py:323](../cogs/announcement_cog.py#L323) |
| お知らせ（この確認はもう使えません） | 終わった確認のボタンが押された | [announcement_cog.py:257](../cogs/announcement_cog.py#L257) |
| お知らせの一覧（お知らせはありません） | `/announce list`で0件 | [announcement_cog.py:485](../cogs/announcement_cog.py#L485) |
| お知らせの一覧 | `/announce list` | [announcement_cog.py:486](../cogs/announcement_cog.py#L486) |
| お知らせを削除しました | `/announce del` | [announcement_cog.py:625](../cogs/announcement_cog.py#L625) |
| プレビュー | `/announce preview` | [announcement_cog.py:663](../cogs/announcement_cog.py#L663) |

---

## 更新のしかた

メッセージを足したり、形式を移し替えたりしたら、この表と集計を直してください。送信箇所は、次の検索で洗い出せます（`tests/`と`.tsumugium/`は除く）。

```bash
rg -n "make_embed\(|make_layout\(|\.send\(|send_message\(|edit_message\(|edit_original_response\(|\.reply\(" --glob '!tests/**' --glob '!.tsumugium/**'
```
