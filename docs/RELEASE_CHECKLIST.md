# リリース確認手順

本番DBを直接使った破壊的な検証は行わず、必要な場合は必ずコピーへ対して実行します。

## 1. 静的確認

- [ ] `config.py`の`VERSION`と`LAST_UPDATED`がリリース内容と一致している
- [ ] `python -m compileall -q .`が成功する（CIで確認）
- [ ] `git diff --check`が成功する（CIで確認）
- [ ] `requests`、`messages.py`、`/setting language`など削除済み要素の実行コード参照がない（CIで確認）
- [ ] `.env`、`db/*.db`、`tmp/`、`backup/`が公開コミットへ含まれていない（CIで確認）
- [ ] `.env.template`と`config.py`の項目・既定値がそろっている（CIで確認）
- [ ] `tests/`に実在するDiscordのID・トークン・サーバー名・ユーザー名と、実際の辞書の登録内容が含まれていない

## 2. 開発用テスト

テストはリポジトリの`tests/`に置きます。PRと`main`・`feature/**`へのpushのたびに、CIがPython 3.10と最新版で実行します。手元ではプロジェクトルートで次を実行します。

```bash
python -m unittest discover -s tests -v
```

- [ ] テキスト前処理
- [ ] チャンネル判定
- [ ] 接続状態の退避・復元
- [ ] 音声キュー型
- [ ] Repositoryと旧DBカラム補完
- [ ] Embed共通ひな形
- [ ] VOICEVOX・Soundboard HTTP Client
- [ ] インストーラー（各モード、`.env`、unit、版の取得と照合）
- [ ] 移行ツール

## 3. インストーラーと配布物

- [ ] インストーラーの結合テスト（`installer-e2e`）が成功している（CIで確認。`main`へのPRでは必ず実行する）
- [ ] 配布物の作成（`build`）が成功し、成果物にzipと`.sha256`がある（CIで確認）
- [ ] ベータ環境で、成果物のzipを使った`sudo tsumugium-setup upgrade --from-file <zipのパス>`が成功し、`status`が稼働中を表示する
- [ ] ベータ環境で、`rollback`で1つ前のバージョンに戻り、もう一度`upgrade`で元に戻せる

## 4. DB互換性

本番相当の4DB（`config.db`、`dict.db`、`soundboards.db`、`users.db`）をバックアップまたは一時ディレクトリへコピーして確認します。

- [ ] `config.db`を開いて既存サーバー設定を読み取れる
- [ ] `Language`カラムと保存済み値が維持される
- [ ] `SpacedSpeed`が不足する旧DBはNULLのカラムを自動追加し、既存設定を維持する
- [ ] `SpacedSpeed`追加前に`backup_config_*_v3-latest.db`が作成され、変更前スキーマとデータを保持する
- [ ] 移行前バックアップ失敗時は`SpacedSpeed`を追加せず起動を中止する
- [ ] `dict.db`の読み辞書・音声辞書を表示できる
- [ ] `full_match`・`trigger_user_id`が不足する旧DBはコピー上で自動補完される
- [ ] `soundboards.db`を起動時のDiscord一覧へ同期できる
- [ ] `users.db`に`user_settings`と`user_dictionary`が作成され、ユーザー削除時に個人辞書も削除される
- [ ] 各コピーで`PRAGMA integrity_check`が`ok`になる
- [ ] 検証前後で既存行が意図せず変更されていない

## 5. Discord実機確認

- [ ] VOICEVOX停止中は疎通エラーを出してBotが起動しない
- [ ] VOICEVOX起動中はエンジンバージョンを出力してBotが起動する
- [ ] 稼働中のVOICEVOXが一時切断しても、30秒以内に復旧すれば受付順に読み上げる
- [ ] VOICEVOXの一時切断が30秒を超えた場合は、試行回数・経過時間・最終原因を1回だけ通知する
- [ ] Botが起動し、コマンド同期が完了する
- [ ] `/version`が`config.py`の`VERSION`と`LAST_UPDATED`を表示する
- [ ] `/user-reading`がサーバー内とDMの両方で実行でき、本人のIDへ保存してephemeralで応答する
- [ ] 設定済みユーザーへのメンションは保存した読み方を使い、未設定ユーザーはDiscordの表示名で読み上げる
- [ ] 入退室通知も保存したユーザーの読み方を使い、未設定ユーザーはDiscordの表示名で読み上げる
- [ ] `/join`・`/leave`が成功する
- [ ] Bot単体メンションで接続・切断できる
- [ ] VC付属チャットと設定チャンネルを読み上げる
- [ ] `!s `、サイレント送信、`s`、`/clear`が仕様どおり動く
- [ ] 空白区切り文章だけ`SpacedSpeed`を使用し、未設定時とリセット後は速度75になる
- [ ] 通常文に異なる3文字以上の空白区切り部分が混在する場合、空白だけを除去して通常速度で読み上げる
- [ ] 読み辞書の追加・削除・検索・ページ移動が動く
- [ ] 音声辞書の完全一致・部分一致・発言者限定が動く
- [ ] `/sounddict add`でSoundboard候補が表示される
- [ ] AutoJoin・自動退出が動く
- [ ] AccessNoticeが入退室を読み上げる
- [ ] 強制切断後の再接続で一時読み上げチャンネルが復元される
- [ ] Bot終了時にHTTP・SQLiteのリソース終了エラーが出ない
- [ ] 定時バックアップ対象に`users.db`が含まれる

## 6. リリース直前

- [ ] SQLiteのバックアップを取得した
- [ ] `git status`で意図しないDB・一時音声・秘密情報がない
- [ ] 差分にDiscord Botトークンや個人情報がない
- [ ] リリース対象ブランチとコミットを確認した
- [ ] ロールバック用に直前バージョンのコードとDBバックアップを保持した

## 7. リリースの公開

GitHub Releasesは、当面は手で作ります。インストーラーの`upgrade`はここに添付したzipを取得します。

1. `config.py`の`VERSION`から`(beta X)`を外して`vX.X.X.XX`にし、`LAST_UPDATED`を更新して`main`へマージする
2. CIの「配布物の作成」ジョブの成果物（`tsumugium-<タグ名>`）から、zipと`.sha256`をダウンロードする
3. `sha256sum -c tsumugium-<タグ名>.zip.sha256`で照合する
4. タグ`vX.X.X.XX`でGitHub Releaseを作り、zipと`.sha256`を添付する。ベータを出すときは、タグを`vX.X.X.XX-beta.X`にして「プレリリース」にする
5. 添付したファイルの名前が`tsumugium-<タグ名>.zip`と`tsumugium-<タグ名>.zip.sha256`であることを確かめる（インストーラーはこの名前で探す）
6. ベータ環境で`sudo tsumugium-setup upgrade`を実行し、Releaseから取得して更新できることを確かめる
7. 本番環境の管理者に、更新が出たことを知らせる

- [ ] 上の手順をすべて終えた

## ロールバック

インストーラーで入れた環境は、`sudo tsumugium-setup rollback`で1つ前のバージョンに戻せます。DBも更新の直前の状態に戻すかを聞かれます。


v4には破壊的変更があるため、v3へロールバックする場合はコードを戻すだけでなく、`backup_config_*_v3-latest.db`を`config.db`として復元します。復元前のv4 DBも別名で保全してください。
