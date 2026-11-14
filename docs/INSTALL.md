# インストールと運用の手引き

インストーラー（`tsumugium-setup`）でTsumugiumを入れ、更新し、管理する手順をまとめます。サーバーを管理する人向けの文書です。Discord Botの作り方と権限は[README](../README.md)の「Discord側の設定」を見てください。

## 対象の環境

- Ubuntu 22.04以上、またはDebian 12以上
- systemdで動いていること
- Python 3.10以上（OSに入っている`python3`を使います）
- VOICEVOX ENGINE（インストーラーは入れません。先に動かしておいてください）
- `sudo`を使えるユーザー

PythonのvenvとFFmpegは、なければインストーラーが`apt`で入れます。

## 初めて入れる

### 1. 配布物を取得して照合する

[GitHub Releases](https://github.com/dora-ntmk/Tsumugium/releases)から、`tsumugium-<タグ名>.zip`と`tsumugium-<タグ名>.zip.sha256`の2つを同じ場所に保存します。保存した場所で照合します。

```bash
sha256sum -c tsumugium-v4.2.0.70.zip.sha256
```

`OK`と表示されれば、ファイルは壊れても書き換えられてもいません。

### 2. 展開する

```bash
unzip tsumugium-v4.2.0.70.zip
```

```bash
cd tsumugium-v4.2.0.70
```

### 3. `.env`を用意する

```bash
cp .env.template .env
```

```bash
nano .env
```

`DISCORD_BOT_TOKEN=`の後ろにBotのトークンを書きます。ほかの項目は`.env.template`の説明を読んで必要なものだけ変えてください。インストーラーは`.env`を作らないので、この手順は省けません。

### 4. インストーラーを実行する

```bash
sudo ./install.sh install
```

最初に`.env`とトークンを確かめ、OS・systemd・Python・必要なパッケージを確かめます。続いて次のことを聞きます。空欄のままEnterを押すと、括弧の中の既定値になります。

| 番号 | 聞くこと | 既定値 | 説明 |
|---|---|---|---|
| 2 | インスタンス名 | `tsumugium` | 1台に複数のBotを入れるときに区別する名前 |
| 3 | 専用ユーザー名 | インスタンス名 | Botを動かすユーザー。いなければ作ります |
| 4 | サービス名 | インスタンス名 | systemdのunitの名前（`<サービス名>.service`） |
| 5 | 入れる場所 | `/opt/<インスタンス名>` | `/home`・`/root`・`/tmp`などの下には入れられません |
| 6 | 取得元 | `dora-ntmk/Tsumugium` | 更新を取得するGitHubのリポジトリ。既定以外にすると警告します |
| 7 | チャンネル | `stable` | `stable`（安定版）か`beta`（ベータ版）。続けて、自動更新を使うかを聞きます（既定は`beta`ならはい、`stable`ならいいえ） |
| 8 | 管理者 | なし | 専用ユーザーのグループに入れるユーザー。入れると、`sudo`なしで`.env`とDBを読めます（次のログインから） |
| 9 | VOICEVOXのURL | `http://127.0.0.1:50021` | 入力したURLに実際に接続して確かめます |
| 10 | 確認 | いいえ | 内容のまとめを見て、よければ`y`と答えます |

VOICEVOXにつながらないときは、`retry`（もう一度接続する）、`skip`（Botを起動せずにインストールだけ済ませる）、`abort`（中止する）から選びます。

インストールが終わるとBotを起動し、Botの準備が終わるまで最大60秒待ちます。「インストールが完了しました」と表示されれば成功です。

質問に答えずに既定値で進めるときは、`--yes`を付けます。既定値以外にしたい項目だけを`--instance`・`--user`・`--service`・`--prefix`・`--repo`・`--channel`・`--admin`・`--voicevox-url`・`--auto-update`（`--no-auto-update`）で指定できます。指定した項目は聞きません。

```bash
sudo ./install.sh install --yes --admin <自分のユーザー名>
```

展開したディレクトリは、インストールが終わったら消してかまいません。2回目からは`sudo tsumugium-setup`を使います。

## 日々の操作

`tsumugium-setup`は`/usr/local/sbin`に置かれます。インスタンスが1つなら、対象を指定する必要はありません。複数あるときは`--instance <インスタンス名>`で指定するか、表示される一覧から選びます。

| すること | コマンド |
|---|---|
| 状態を見る | `sudo tsumugium-setup status` |
| 動かす | `sudo tsumugium-setup start` |
| 止める | `sudo tsumugium-setup stop` |
| 止めて、マシンを再起動しても動かないようにする | `sudo tsumugium-setup stop --disable` |
| 最新のバージョンに更新する | `sudo tsumugium-setup upgrade` |
| バージョンを指定して更新する | `sudo tsumugium-setup upgrade --version v4.2.0.71` |
| 手元のzipで更新する | `sudo tsumugium-setup upgrade --from-file <zipのパス>` |
| 今のバージョンを入れ直す | `sudo tsumugium-setup repair` |
| 1つ前のバージョンに戻す | `sudo tsumugium-setup rollback` |
| 自動更新を使う・やめる | `sudo tsumugium-setup auto-update on`・`off` |
| 消す（DBと`.env`は残す） | `sudo tsumugium-setup uninstall` |
| DBと`.env`も含めてすべて消す | `sudo tsumugium-setup uninstall --purge` |

`status`は、バージョン、チャンネル、状態（稼働中／停止中（手動）／停止中（自動起動も無効）／異常で停止）、自動更新のオン・オフと最後に確かめた結果を表示します。

`--yes`を付けると、質問せずに既定値で進めます。ただし`uninstall --purge`は`--yes`と一緒には使えません。消してよいかを2回確かめるためです。

### 設定を変える

```bash
sudo nano /opt/tsumugium/.env
```

```bash
sudo systemctl restart tsumugium
```

DBの場所、一時ファイルとバックアップの置き場所、VOICEVOXのURLは、インストーラーがsystemdのunitで渡します。`.env`に書いても使われません。VOICEVOXのURLを変えたいときは、記録（`/etc/tsumugium/<インスタンス名>.json`）の`voicevox_url`を`sudo nano`で直してから、`sudo tsumugium-setup repair`を実行します。`repair`がunitを書き直します。

### ログを見る

```bash
sudo journalctl -u tsumugium -f
```

Ctrl+Cで表示を終えます。

## どこに何があるか

インスタンス名が`tsumugium`、入れる場所が既定の場合の例です。

| 場所 | 中身 | 持ち主と権限 |
|---|---|---|
| `/opt/tsumugium/.env` | 設定 | `root:tsumugium`、640 |
| `/opt/tsumugium/app/` | コードと専用のvenv（更新のたびに作り直す） | `root`、Botは読むだけ |
| `/opt/tsumugium/app.prev/` | 1つ前のバージョン（`rollback`で使う） | `root` |
| `/opt/tsumugium/data/db/` | DB | `tsumugium`、750 |
| `/opt/tsumugium/data/backup/` | DBのバックアップ | `tsumugium`、750 |
| `/opt/tsumugium/data/tmp/` | 読み上げ音声の一時ファイル | `tsumugium`、750 |
| `/opt/tsumugium/data/state/` | Botとインストーラーがやり取りするファイル（`ready.json`、更新中の印の`updating.json`、更新の結果の`last-update.json`、自動更新が確かめた結果の`auto-update.json`、止める理由の`restart-reason.json`、つないでいたVCの`voice-sessions.json`など） | `tsumugium`、750 |
| `/etc/tsumugium/tsumugium.json` | インストールで選んだ値の記録 | `root`、644 |
| `/etc/systemd/system/tsumugium.service` | systemdのunit | `root`、644 |
| `/etc/systemd/system/tsumugium-update.timer`・`tsumugium-update.service` | 自動更新のtimerとservice | `root`、644 |
| `/usr/local/sbin/tsumugium-setup` | 操作の入口 | `root`、755 |

Botが書き込めるのは`data/`の中だけです。インストーラーが`data/`を消すのは`uninstall --purge`のときだけです。更新に失敗したときと`rollback`でDBを戻すときは、`data/db/`の中身をバックアップの内容に置き換えます。

## 更新で起きること

`upgrade`は次の順に進みます。

1. 取得: GitHub Releasesから新しいバージョンのzipと`.sha256`を取得して照合し、`app.new/`に展開します。
2. 準備: 新しいバージョンのインストーラーに処理を引き継ぎ、venvを作り、VOICEVOXに接続できるかを確かめます。`.env`に足りない項目があれば、末尾に書き足します（書き足す前の内容は`.env.bak`に残します）。ここまではBotを止めません。
3. 切り替え: Botを止め、DBを`data/backup/upgrade-<日時>-<前のバージョン>/`にコピーし、`app/`を`app.prev/`に、`app.new/`を`app/`に切り替えます。
4. 確認: Botを起動し、準備が終わるまで最大60秒待ちます。

4で起動を確認できなかったときや、3の途中で失敗したときは、コード・venv・DB・unitを自動で更新の前に戻し、前のバージョンで起動し直します。`.env`に書き足した項目は残します。

`.env`に必須の項目が増えて値がないときは、項目を書き足したうえで、切り替えの前に止まります。値を書いてから、もう一度`upgrade`を実行してください。

Botを止めている間に`upgrade`すると、更新のあとで起動して確かめるかを聞きます。`--yes`を付けたときは、更新せずに終わります。

### バックアップ

| 種類 | 置き場所 | 残す数 |
|---|---|---|
| 定時のバックアップ | `data/backup/` | `BACKUP_KEEP`（既定は7つ） |
| 更新の前のバックアップ | `data/backup/upgrade-<日時>-<前のバージョン>/` | 5つ |

定時のバックアップは既定で毎日12:00に取ります。時刻は`.env`の`BACKUP_TIMES`で変えられます。

`rollback`では、戻すバージョンから更新したときのバックアップを使って、DBも戻すかを聞きます。既定は「戻す」です。新しいバージョンがDBの形を変えていると、古いバージョンがそのDBを読めないことがあるためです。

## 止めるときと再起動のあと

- 更新・`rollback`・`systemctl restart`・マシンの再起動でBotが止まるときは、読み上げチャンネルに「再起動します」と知らせてからVCを抜けます。起動したあと、止まっていた時間が3分以内なら、人がいるVCに自動でつなぎ直します。
- `sudo tsumugium-setup stop`で止めたときは、知らせずにVCを抜け、次に起動してもつなぎ直しません。

## 更新の知らせ

- 更新しているあいだ（手でも自動でも）、Botのステータスは「取り込み中」と「📥アップデート中…　操作を受け付けない場合があります」になります。更新が終わってBotが起動すると、元の表示に戻ります。
- `.env`の`OPERATOR_USER_ID`を設定していると、更新・入れ直し・戻したときの結果を、Botが起動したあとにDMで知らせます。成功したときも知らせます。
- 自動更新が「前回の更新が途中で止まっている」ために止まっているときも、DMで知らせます。

## 自動更新

自動更新をオンにすると、systemdのtimer（`<サービス名>-update.timer`）が、マシンの起動の5分後と、そのあとは10分おきに`tsumugium-setup upgrade --auto`を実行します。チャンネルに新しいバージョンがあれば、「更新で起きること」と同じ流れで更新します。

- 既定は`beta`チャンネルならオン、`stable`チャンネルならオフです。`sudo tsumugium-setup auto-update on`と`off`で切り替えます。イベントの最中など、しばらく更新したくないときも`off`にします。
- 次のときは更新しません。
  - Botを手で止めているとき
  - 前回の更新が途中で止まっているとき（`repair`か`rollback`で直してください）
  - ほかの操作が実行中のとき
  - 新しいバージョンが「飛ばすバージョン」のとき
- 起動を確かめられずに元に戻したバージョンと、`rollback`で戻した元のバージョンは、記録の「飛ばすバージョン」に入ります。自動更新は、それより新しいバージョンが出るまで更新しません。手で`upgrade`すれば入れられます。
- 最後に確かめた時刻と結果、飛ばすバージョンは`status`で見られます。くわしいログは`sudo journalctl -u tsumugium-update`で見ます。
- 自動更新のないバージョンに`rollback`したときは、timerは残りますが何もしません。

### 自動更新がオフのとき

自動更新をオフにしても、timerは10分おきに新しいバージョンを確かめます。新しいバージョンがあれば更新はせず、`.env`の`OPERATOR_USER_ID`の人へ、ボタン付きのDMで知らせます。同じバージョンは1回だけ知らせ、新しいバージョンがなければ何も送りません。

- 「今すぐ更新する」を押すと、すぐに更新が始まります。
- 「VCが空いたら更新する」を押すと、BotがどのVCにもつないでいないときに更新します。待っているあいだも「今すぐ更新する」は押せます。
- ボタンを押せるのは`OPERATOR_USER_ID`の人だけです。ボタンで頼んだ更新は手で`upgrade`したときと同じく、「飛ばすバージョン」でも入れます。
- 更新の結果はほかの更新と同じくDMで届きます。

## 困ったとき

| 表示 | 原因と対処 |
|---|---|
| VOICEVOXに接続できません | VOICEVOX ENGINEが動いているか、URLが合っているかを確かめます。`curl -s http://127.0.0.1:50021/version`でバージョン番号が返れば動いています |
| 60秒待ちましたが、Botの準備が終わりませんでした | `sudo journalctl -u tsumugium -n 50`でBotのログを見ます。トークンの誤りやDiscordへの接続の失敗がよくある原因です |
| 前回の更新が途中で止まっています | `upgrade`を実行すると、`repair`（記録のバージョンを入れ直す）と`rollback`（1つ前のバージョンに戻す）のどちらで直すかを聞かれます |
| ほかの処理が実行中です | 同じインスタンスで別の操作が動いています。終わってから実行し直します |
| tsumugium-setup が作ったものではないため、上書きしません | 同じ名前のunitがすでにあります。インストールのときに別のサービス名を選ぶか、元のunitの名前を変えます |
| GitHubへの問い合わせが上限に達しました | 認証なしの問い合わせは1時間に60回までです。しばらく待ってから実行し直します |
| `.env`に問題があるため、止めます | 表示された項目を`.env`で直してから実行し直します |

## 旧環境からの移行

インストーラーを使わずに動かしていたv4以上の環境から移るときは、移行ツール`scripts/migrate_legacy.py`を使います。移行ツールは配布用のzipに入っていないので、リポジトリから取得してください。

1. 古いBotを止め、自動起動を無効にします。古いunitが新しいサービス名と同じ名前なら、名前を変えます（例: `tsumugium-old.service`）。
2. 新しいバージョンのzipを展開します。
3. 展開したディレクトリに、古い環境のディレクトリを`old/`として丸ごとコピーします。

   ```bash
   cp -a <古いディレクトリ> old
   ```

4. 展開したディレクトリに`migrate_legacy.py`を置いて実行します。

   ```bash
   python3 migrate_legacy.py
   ```

   `old/`から、新しい`.env`と`data/`（DBと今までのバックアップ）を作ります。DBが壊れていないかも確かめます。`old/`の中身は変えないので、何度実行してもかまいません。

5. 表示に従って`sudo ./install.sh install`を実行します。インストーラーが`.env`と`data/`を取り込みます。

古い環境のディレクトリとunitは消さないので、問題があれば`sudo tsumugium-setup uninstall`のあと古いunitを戻せば、元の状態で動かせます。
