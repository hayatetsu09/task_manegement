# submission-calendar スキル

`SKILL.md` は Claude 向けの手順書です。Claude に「提出物をまとめて」と頼むか、
毎朝の Routine から呼ばれると、この手順に沿って次を行います。

1. Gmail（と Microsoft 365 コネクタがあれば Outlook）から提出依頼らしいメールを集める
2. `subcal parse` で締め切りを抽出する
3. Google カレンダーの既存予定と照合し、未登録のものだけ作成する
4. 今日・明日の提出物を報告する

Google Cloud の API 設定（`credentials.json`）は不要です。
ローカルで cron から動かしたい場合は、リポジトリ直下の README を参照してください。
