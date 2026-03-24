# Tsumugium

VOICEVOXを用いたDiscord読み上げBotです。

## 必要ライブラリ

#### 【外部パッケージ】
* Discord.py >= 2.7.0
* requests >= 2.32.5
* python-dotenv >= 1.2.2
* aiohttp >= 3.9.0
* PyNaCl >= 1.6.0
* davey >= 0.1.0

#### 【標準ライブラリ】

asyncio, io, json, os, re, sqlite3, unicodedata, typing,
datetime, pathlib, collections, shutill

## 必要権限

Discord Developer PotalのBot管理画面にて、以下のスコープと権限の許可を設定してください。

#### 【スコープ】
* applications.commands
* bot

#### 【権限】
* メッセージを送る
* メッセージ履歴を読む
* リアクションを付ける ※
* 接続
* 発言
* スピーカー参加をリクエスト ※
* サウンドボードを使用

※将来の拡張性のため設定しています

## 初回設定
.envファイルを作成し、起動に必要な内容を記載してください。

[例はこちら。](https://github.com/dora-ntmk/Tsumugium/blob/main/.env.template)

そのほかの設定は、初回起動時に実行されます。

> [!IMPORTANT]
> つむぎちゃん v2以前の設定をコピーされる方は別の方法で初回設定を行う必要があります。
> 移行方法は直接お問い合わせください。

## 起動方法
必要ライブラリをすべてインストールしたうえで、
```bash
python main.py
```
を使用すると起動できます。

不足ファイル群は自動的に作成されます。

なお、これとは別に[VOICEVOX ENGINE](https://github.com/VOICEVOX/voicevox_engine)またはそれを内包したプログラムを動作させておく必要があります。

## 使用方法

使用方法については、[ユーザーガイド](USERGUIDE.md)をご覧ください。

## ライセンス
MIT Licenseにて公開しています。

## 使用OSS
- [emoji-ja](https://github.com/yagays/emoji-ja) - MIT License, Copyright 2018 yag_ays
