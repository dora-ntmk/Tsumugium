# Tsumugium ユーザーガイド

v3.0.0（2026/03/24）時点の情報です。

## 目次

* [基本的な使い方](#基本的な使い方)
  * [接続・切断](#接続切断)
    * [/join](#join)
    * [/leave](#leave)
    * [メンション時の動作](#メンション時の動作)
  * [読み上げ](#読み上げ)
    * [読み上げ対象](#読み上げ対象)
    * [事前読み上げスキップ](#事前読み上げスキップ)
    * [読み上げ停止](#読み上げ停止)
      * [読み上げ中のみ停止](#読み上げ中のみ停止)
      * [キュークリア](#キュークリア)


* [一般ユーザー向け設定](#一般ユーザー向け設定)
  * [辞書](#辞書)
    * [/dict add](#dict-add)
    * [/dict del](#dict-del)
    * [/dict view](#dict-view)
  * [音声辞書](#音声辞書)
    * [/sounddict add](#sounddict-add)
    * [/sounddict del](#sounddict-del)
    * [/sounddict view](#sounddict-view)


* [サーバー管理者向け設定](#サーバー管理者向け設定)
  * [読み上げチャンネル](#読み上げチャンネル)
    * [/setting text-target](#setting-text-target)
    * [/setting text-target-reset](#setting-text-target-reset)
    * [一時的読み上げ設定](#一時的読み上げ設定)
    * [VCのチャットの扱い](#vcのチャットの扱い)
  * [接続チャンネル](#接続チャンネル)
    * [/setting voice-target](#setting-voice-target)
    * [/setting voice-target-reset](#setting-voice-target-reset)
  * [話者](#話者)
    * [/setting speaker](#setting-speaker)
    * [デフォルトの話者](#デフォルトの話者)
  * [音量](#音量)
    * [/setting volume](#setting-volume)
  * [速さ](#速さ)
    * [/setting speed](#setting-speed)
  * [最大文字数](#最大文字数)
    * [/setting max-char](#setting-max-char)
  * [自動入室](#自動入室)
    * [/setting auto-join](#setting-auto-join)
  * [入退室通知](#入退室通知)
    * [/setting access-notice](#setting-access-notice)
  * [言語](#言語)
    * [/setting language](#setting-language)


* [辞書機能の仕様](#辞書機能の仕様)
  * [音声辞書について](#音声辞書について)
  * [優先辞書について](#優先辞書について)
  * [標準読み替え対応表](#標準読み替え対応表)

## 基本的な使い方

Tsumugiumの基本的な使い方を説明します。

### 接続・切断

接続と切断について説明します。

#### /join

`/join`を使用すると、ボイスチャンネルに接続することができます。
`change_channel: True`にすると、デフォルトの読み上げチャンネルと接続チャンネルを変更します（サーバー管理権限が必要）。
