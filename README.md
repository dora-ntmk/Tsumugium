# Tsumugium

VOICEVOXを用いたDiscord読み上げBotです。

## 必要なもの

* Python 3.11+
* [uv](https://docs.astral.sh/uv/)（パッケージ管理）
* ffmpeg（これがないと音声が再生されません）

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

```bash
# 依存関係をインストール
uv sync

# 起動
uv run python main.py
```

不足ファイル群は自動的に作成されます。

なお、これとは別に[VOICEVOX ENGINE](https://github.com/VOICEVOX/voicevox_engine)またはそれを内包したプログラムを動作させておく必要があります。

## 使用方法

使用方法については、[ユーザーガイド](USERGUIDE.md)をご覧ください。

## ライセンス
MIT Licenseにて公開しています。

## 使用OSS
- [emoji-ja](https://github.com/yagays/emoji-ja) - MIT License, Copyright 2018 yag_ays