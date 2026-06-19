# つむぎちゃんv2→Tsumugium v3ベースbotへの移行方法

# サーバー別設定ファイル

### v2→v3での変更点

- jsonで管理→SQLiteで管理
- サーバー別でファイルを分けて管理→すべてのサーバーで一つのDBで管理
- 内容の変更

### 変更前後の項目名

| v2での名称           | v2 | v3 | v3での名称       |
| ---------------- | -- | -- | ------------ |
| \-               | ❌  | ✅  | guild_id     |
| text_target      | ✅  | ✅  | TextTarget   |
| voice_target     | ✅  | ✅  | VoiceTarget  |
| speaker_id       | ✅  | ✅  | Speaker      |
| volume           | ✅  | ✅  | Volume       |
| \-               | ❌  | ✅  | Speed        |
| max_word         | ✅  | ✅  | MaxChar      |
| autojoin         | ✅  | ✅  | AutoJoin     |
| join_notice      | ✅  | ✅  | AccessNotice |
| \-               | ❌  | ✅  | Language     |
| f0_speaker_id    | ✅  | ❌  | \-           |
| f0_correct       | ✅  | ❌  | \-           |
| testmode         | ✅  | ❌  | \-           |
| aisatsu          | ✅  | ❌  | \-           |
| kutansusuna_mode | ✅  | ❌  | \-           |

### v2での形式

1064526948711284886_config.json

```javascript
{"text_target": 1064529028297523300, "voice_target": 1064526951567597582, "autojoin": 1, "speaker_id": 8, "f0_speaker_id": 8, "f0_correct": 0, "volume": 90, "max_word": 100, "testmode": 0, "join_notice": 1, "aisatsu": 1}
```

### v3での形式

config.db

| guild_id            | TextTarget          | VoiceTarget         | Speaker | Volume | Speed | MaxChar | AutoJoin | AccessNotice | Language※ |
| ------------------- | ------------------- | ------------------- | ------- | ------ | ----- | ------- | -------- | ------------ | --------- |
| 1064526948711284886 | 1064529028297523300 | 1064526951567597582 | 8       | 90     | 100   | 100     | 1        | 1            | ja        |

※Speedはv2には該当設定がなかったため、初期値を割り当て

---

# 辞書ファイル

### v2→v3での変更点

- jsonで管理→SQLiteで管理
- サーバー別でファイルを分けて管理→すべてのサーバーで一つのDBで管理
- 優先辞書の追加
- 音声辞書を統合

### v2での形式

1064526948711284886_dict.json

```javascript
{
  "dict": {
    "ｈｏｒｎ": "ほーん",
    "🍵": "おゆ",
    "<:thinking_kita:1270578690560098385>": "かんがえるきたーん"
  }
}
```

1064526948711284886_dict_audio.json

```javascript
{
  "dict": {
    "<:thinking_kita:1270578690560098385>": "audio/dict/1064526948711284886_かんがえるきたーん_2024-08-15_10-32-21.wav"
  }
}
```

global_dict.json

```javascript
{
  "🤔": "かんがえるかお"
}
```

### v3での形式

| guild_id            | word                                 | reading   | sound_id | is_priority | added_at   |
| ------------------- | ------------------------------------ | --------- | -------- | ----------- | ---------- |
| 1064526948711284886 | <:thinking_kita:1270578690560098385> | かんがえるきたーん | <null>   | 1           | 1774096736 |
| 1064526948711284886 | 🍵                                   | おゆ        | <null>   | 1           | 1774096736 |
| 1064526948711284886 | ｈｏｒｎ                                 | ほーん       | <null>   | 0           | 1774096736 |
| __common__          | 🤔                                   | かんがえるかお   | <null>   | 1           | 1774096736 |

※1：wordはv2の辞書ファイルの末尾から先頭にかけて（逆順で）追加する

※2：サウンドの再生方法が変更されたため、旧音声辞書に含まれていてもsound_idは<null>とする

※3：is_priorityは、マイグレーション用プログラムに、優先辞書の対象であるかどうかを確認するプログラムを、現在のword_dict.py＞def _is_priority_wordを参考に組み立てる。対象なら1、そうでなければ0。

※4：added_atはv2に該当項目がないため、マイグレーション時のUNIX時間を追加する

---

# その他共通事項

- main.pyなどと同じところにmigration2v3.pyを作る
- マイグレーション前のファイル群はすべて、「v2_files」フォルダに入れてもらう。
「v2のconfigディレクトリ内のすべてのファイルを、今作成されたv2_filesディレクトリの中に入れてください」というメッセージを出して、準備が完了したらyで実行するように
- {guild_id}_config.json、{guild_id}_dict.json、{guild_id}_dict_audio.json、global_dict.json以外のファイルが含まれていたらすべて無視する
- 失敗してもやり直せる仕組みにする
- 元ファイルの名称は成功失敗かかわらず変更しないでそのままにする

?descriptionFromFileType=function+toLocaleUpperCase()+{+[native+code]+}+File&mimeType=application/octet-stream&fileName=つむぎちゃんv2→Tsumugium+v3ベースbotへの移行方法.md&fileType=undefined&fileExtension=md