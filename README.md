# submission-calendar

メールに届いた**提出依頼を自動で見つけて、Google カレンダーに締め切りの予定としてまとめる**コマンドラインツールです。

課題・レポート・申請書類・アンケートなど、あちこちのメールに散らばった「いつまでに何を出すか」を
ひとつのカレンダーに集約し、通知も付けておけるようにします。

```console
$ subcal sync
Gmail を検索中 (最大 50 件)
  クエリ: newer_than:60d -category:promotions -category:social (提出 OR 課題 OR ...)
  34 件のメールを取得しました

+ 作成    【情報処理演習】第3回レポート提出のお願い
          締切: 2026-09-20 17:00  （メールの表記: 9月20日(日) 17:00）
= 変更なし  健康診断 問診票の提出について
          締切: 2026-10-20 23:59  （メールの表記: 10/20）
- スキップ  奨学金申請書類について — 締め切りが不明
          締切: 締切不明
          ※ 本文から日付を読み取れませんでした
          ※ 締め切りが分からないため予定を作成できません

--- まとめ ---
作成 1 / 更新 0 / 変更なし 1 / スキップ 1 / 失敗 0 / 対象外 31
```

## 特徴

- **日本語の締め切り表現に対応** — 「9月20日(日) 17:00まで」「来週金曜」「今月末」「10/5必着」「24時まで」など
- **何度実行しても予定が増えない** — 予定に元メールの ID を埋め込んでおき、作成済みなら更新のみ
- **締め切りが変わったら予定も更新** — 訂正メールが来て日付が変わった場合も追従します
- **読み取れなかったものは「要確認」として表示** — 勝手に日付を推測して間違った予定を作りません
- **予定から元のメールへ 1 クリックで戻れる** — 予定の説明に Gmail へのリンクが入ります
- **Gmail は読み取り専用** — 既定ではメールを変更しません（ラベル付けを有効にしたときだけ書き込み権限を要求）

## セットアップ

### 1. インストール

```bash
git clone https://github.com/hayatetsu09/task_manegement.git
cd task_manegement
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Python 3.10 以上が必要です。

### 2. Google 側の準備

Gmail と Google カレンダーを読み書きするための認証情報を作ります（無料、初回のみ）。

1. [Google Cloud コンソール](https://console.cloud.google.com/) でプロジェクトを作る（既存のものでも可）
2. 「API とサービス」→「ライブラリ」から **Gmail API** と **Google Calendar API** を有効にする
3. 「OAuth 同意画面」を設定する
   - User Type は **外部**（個人の Gmail アカウントの場合）
   - 「対象」→「テストユーザー」に**自分のメールアドレスを追加**する（これを忘れると認証時に弾かれます）
4. 「認証情報」→「認証情報を作成」→「OAuth クライアント ID」→ アプリケーションの種類に **デスクトップアプリ** を選ぶ
5. JSON をダウンロードし、`credentials.json` という名前でこのリポジトリのディレクトリに置く

> `credentials.json` と、認証後にできる `token.json` は**あなたのアカウントの鍵**です。
> `.gitignore` で除外済みですが、他人に渡さないでください。

### 3. 認証

```bash
subcal auth
```

ブラウザが開くので、使いたい Google アカウントを選んで許可します。
以降はトークンが使い回されるので、この操作は不要です。

### 4. 動作確認

まずは書き込まずに結果だけ見ます。

```bash
subcal scan
```

問題なさそうならカレンダーに反映します。

```bash
subcal sync
```

## 使い方

| コマンド | 説明 |
| --- | --- |
| `subcal scan` | メールを解析して結果を表示する（カレンダーは変更しない） |
| `subcal sync` | 提出依頼をカレンダーに反映する |
| `subcal sync --dry-run` | 何が作成・更新されるかだけ表示する |
| `subcal auth` | Google の認証をやり直す |
| `subcal init-config` | 設定ファイルのひな形を書き出す |

よく使うオプション:

```bash
subcal sync --since 14                    # 直近 14 日のメールだけ
subcal sync -q "label:大学 newer_than:30d"  # Gmail の検索クエリを指定
subcal sync -n 200                        # 調べるメールを 200 件まで増やす
subcal sync --calendar <カレンダーID>       # 書き込み先カレンダーを指定
subcal sync --all                         # 処理済みの記録を無視して見直す
subcal sync -v                            # 判定スコアなどを詳しく表示
```

### 専用カレンダーに分けるのがおすすめ

Google カレンダーで「提出物」などのカレンダーを新しく作り、その ID を設定に書いておくと、
普段の予定と混ざらず、色や通知もまとめて管理できます。
カレンダー ID は Google カレンダーの「設定と共有」→「カレンダーの統合」で確認できます。

```yaml
calendar:
  calendar_id: "xxxxxxxx@group.calendar.google.com"
```

### 定期実行

cron で 1 日 2 回動かす例:

```cron
0 8,20 * * * cd ~/task_manegement && .venv/bin/subcal sync >> ~/.local/state/subcal.log 2>&1
```

同じメールから予定が二重に作られることはないので、何度実行しても問題ありません。

## 設定

設定ファイルがなくても動きます。変えたいときだけ作ってください。

```bash
subcal init-config           # config.yaml を書き出す
```

読み込み順は `./config.yaml` → `~/.config/submission-calendar/config.yaml`、`-c` で明示もできます。
全項目の説明は [`config.example.yaml`](config.example.yaml) にあります。よく触るのは次のあたりです。

| 項目 | 既定値 | 説明 |
| --- | --- | --- |
| `gmail.query` | `newer_than:60d …` | 対象にするメールの検索条件。`label:大学` などで絞ると精度が上がる |
| `gmail.label_processed` | （なし） | 処理済みメールに付ける Gmail ラベル名 |
| `calendar.calendar_id` | `primary` | 書き込み先のカレンダー |
| `calendar.event_prefix` | `[提出] ` | 予定のタイトルの先頭に付ける文字 |
| `calendar.default_due_time` | `23:59` | 時刻が書かれていなかったときに使う締め切り時刻 |
| `calendar.all_day_when_time_unknown` | `false` | `true` にすると時刻不明のものを終日予定にする |
| `calendar.reminders_minutes` | `[1440, 180]` | 通知するタイミング（分前）。1440 = 前日 |
| `detection.min_score` | `3.0` | 提出依頼と判定するしきい値。下げると拾いやすく、上げると誤検出が減る |
| `detection.extra_keywords` | `[]` | 「実習日誌」など自分の用途に合わせた語を足せる |
| `detection.exclude_keywords` | 広告系の語 | 件名・差出人にあれば対象外にする語 |

## 判定のしくみ

1. **絞り込み** — `gmail.query` に「提出・課題・締切…」などのキーワード条件を足して Gmail を検索します
2. **提出依頼かどうかの採点** — 「提出期限」「締切」「必着」などの語を重み付きで数えます（件名にある語は 2 倍）。
   さらに「9/20まで」のように日付と締め切り表現が直結していれば加点します
3. **締め切りの選択** — 本文中のすべての日付を拾い、周辺の語から「締め切りらしさ」を採点して 1 つ選びます。
   「10/1〜10/20までに」のような期間は終わりの日を、受信日より前の日付は候補から外します
4. **カレンダーへ反映** — 予定に元メールの ID と内容の指紋を埋め込みます。次回以降はそれを照合して、
   同じなら何もせず、締め切りが変わっていれば予定を更新します

判定に自信がないものは予定を作らず「要確認」として一覧に出します。

## Claude による抽出（任意）

「次回の講義の3日前まで」のような、正規表現では扱いにくい書き方まで拾いたい場合は、
Claude（Anthropic API）に判定させることもできます。**既定では無効**で、使わなくても全機能が動きます。

```bash
pip install -e ".[llm]"
export ANTHROPIC_API_KEY=sk-ant-...
subcal sync --extractor auto
```

- `--extractor auto` … ルールで締め切りを読み取れなかったメールだけ Claude に回します（費用を抑えられる、おすすめ）
- `--extractor llm` … すべてのメールを Claude で判定します

モデルは `config.yaml` の `llm.model`（既定 `claude-opus-5`）で変更できます。
安全性の判定で応答が拒否された場合に備え、サーバ側の自動フォールバックを有効にしてあります。

> この機能を有効にすると、対象メールの本文が Anthropic の API に送信されます。
> 無効のまま（既定）であれば、メールが外部に送られることはありません（Google 以外への通信は発生しません）。

## うまく動かないとき

| 症状 | 対処 |
| --- | --- |
| 提出依頼なのに拾われない | `subcal scan -v` でスコアを確認し、`detection.min_score` を下げるか `detection.extra_keywords` に語を足す |
| 関係ないメールが拾われる | `detection.min_score` を上げる、`detection.exclude_keywords` / `exclude_senders` に足す、`gmail.query` を `label:` などで絞る |
| 締め切りの日付がずれる | 予定の説明にある「メール中の締め切り表記」を確認。日付の書き方が独特な場合は `--extractor auto` を試す |
| `アクセスをブロック` と表示される | OAuth 同意画面のテストユーザーに自分のアドレスを追加する |
| 権限が足りないと言われる | `gmail.label_processed` を設定した直後は権限が増えるため `subcal auth` をやり直す |
| 予定を消したのにまた作られない | 処理済みの記録が残っているため。`subcal sync --all` で作り直す |

## 開発

```bash
pip install -e ".[dev]"
python -m pytest
```

テストはネットワークに接続しません。Google API と Anthropic API はテスト用の偽オブジェクトに差し替えています。

```
src/submission_calendar/
├── cli.py            コマンドラインの入口
├── config.py         設定の読み込みと検証
├── dates.py          日本語・英語の日付／時刻パーサ
├── models.py         Message / Deadline / Submission
├── state.py          処理済みメールの記録
├── google_auth.py    OAuth 認証
├── calendar_sync.py  Google カレンダーへの反映（重複防止つき）
├── sources/gmail.py  Gmail からの取り込み
└── extract/
    ├── rules.py      キーワードと正規表現による抽出（既定）
    └── llm.py        Claude による抽出（任意）
```
