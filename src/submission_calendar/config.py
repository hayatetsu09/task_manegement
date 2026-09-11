"""設定の読み込み。

すべての項目に既定値があるので、設定ファイルが無くても動く。
YAML では変えたい項目だけ書けばよい（既定値に上書きマージされる）。
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # YAML 設定を使わない場合は無くても動く
    yaml = None

__all__ = ["Config", "ConfigError", "DEFAULT_CONFIG_PATHS", "GmailConfig", "ImapConfig",
           "CalendarConfig", "DetectionConfig", "LLMConfig", "EXAMPLE_CONFIG"]

DEFAULT_CONFIG_PATHS = (
    Path("config.yaml"),
    Path("~/.config/submission-calendar/config.yaml"),
)

# 広告・宣伝メールを落とすための語。件名と差出人だけを見る
# （本文フッターの「配信停止」などで正当な依頼メールを落とさないため）。
DEFAULT_EXCLUDE_KEYWORDS = [
    "メールマガジン",
    "メルマガ",
    "セール",
    "キャンペーン",
    "広告",
    "newsletter",
    "unsubscribe",
]


class ConfigError(Exception):
    """設定ファイルの記述ミス。"""


@dataclass
class GmailConfig:
    # Gmail から取り込むか
    enabled: bool = True
    # Gmail の検索クエリ。https://support.google.com/mail/answer/7190 の記法が使える
    query: str = "newer_than:60d -category:promotions -category:social"
    # query に提出依頼らしいキーワード群（OR 条件）を自動で足すか
    add_keyword_filter: bool = True
    max_results: int = 50
    # 処理済みメールに付けるラベル名（空なら付けない。gmail.modify 権限が要る）
    label_processed: str = ""


@dataclass
class ImapConfig:
    """IMAP での取り込み設定。Outlook / Microsoft 365 や大学のメールサーバ向け。"""

    enabled: bool = False
    # Outlook / Microsoft 365 は outlook.office365.com
    host: str = "outlook.office365.com"
    port: int = 993
    use_ssl: bool = True
    username: str = ""
    # パスワードは設定ファイルに書かず、この名前の環境変数から読む
    password_env: str = "SUBCAL_IMAP_PASSWORD"
    mailbox: str = "INBOX"
    # 直近何日分を対象にするか
    days: int = 60
    max_results: int = 50


@dataclass
class CalendarConfig:
    calendar_id: str = "primary"
    event_prefix: str = "[提出] "
    # 時刻が読み取れなかったときに終日予定にするか（False なら default_due_time を使う）
    all_day_when_time_unknown: bool = False
    default_due_time: str = "23:59"
    # 締め切り時刻の何分前から予定を入れるか
    duration_minutes: int = 30
    # 通知（分前）。最大 5 件まで
    reminders_minutes: list[int] = field(default_factory=lambda: [1440, 180])
    # Google カレンダーの色 ID（"1"〜"11"。空なら既定色）
    color_id: str = ""


@dataclass
class DetectionConfig:
    # 提出依頼と判定するスコアのしきい値（件名の語は 2 倍で数える）
    min_score: float = 3.0
    # 判定に加点したい独自キーワード（重み 1）
    extra_keywords: list[str] = field(default_factory=list)
    # 件名・差出人にこれらが含まれていたら対象外にする
    exclude_keywords: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDE_KEYWORDS))
    exclude_senders: list[str] = field(default_factory=list)
    # 受信日より何日以上前の日付を締め切り候補から外すか
    ignore_past_days: int = 1


@dataclass
class LLMConfig:
    """extractor に llm / auto を選んだときだけ使う（要 ANTHROPIC_API_KEY）。"""

    model: str = "claude-opus-5"
    effort: str = "low"
    max_tokens: int = 2048


@dataclass
class Config:
    timezone: str = "Asia/Tokyo"
    # rules: 正規表現のみ / llm: Claude に任せる / auto: rules で取りこぼした分だけ Claude
    extractor: str = "rules"
    credentials_file: str = "credentials.json"
    token_file: str = "~/.config/submission-calendar/token.json"
    state_file: str = "~/.config/submission-calendar/state.json"
    gmail: GmailConfig = field(default_factory=GmailConfig)
    imap: ImapConfig = field(default_factory=ImapConfig)
    calendar: CalendarConfig = field(default_factory=CalendarConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    # --- パス関連 -----------------------------------------------------
    @property
    def credentials_path(self) -> Path:
        return Path(self.credentials_file).expanduser()

    @property
    def token_path(self) -> Path:
        return Path(self.token_file).expanduser()

    @property
    def state_path(self) -> Path:
        return Path(self.state_file).expanduser()

    # --- 読み込み -----------------------------------------------------
    @classmethod
    def load(cls, path: Path | str | None = None) -> "Config":
        if path is not None:
            path = Path(path).expanduser()
            if not path.exists():
                raise ConfigError(f"設定ファイルが見つかりません: {path}")
            return cls.from_dict(_read_yaml(path))

        for candidate in DEFAULT_CONFIG_PATHS:
            candidate = candidate.expanduser()
            if candidate.exists():
                return cls.from_dict(_read_yaml(candidate))
        return cls()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Config":
        config = _merge(cls(), data or {}, path="")
        config.validate()
        return config

    def validate(self) -> None:
        from .dates import parse_time_string  # 循環インポートを避けるため遅延

        try:
            parse_time_string(self.calendar.default_due_time)
        except ValueError as exc:
            raise ConfigError(
                f"calendar.default_due_time は HH:MM 形式で指定してください: "
                f"{self.calendar.default_due_time!r}"
            ) from exc

        if self.extractor not in ("rules", "llm", "auto"):
            raise ConfigError(f"extractor は rules / llm / auto のいずれかです: {self.extractor!r}")

        if len(self.calendar.reminders_minutes) > 5:
            raise ConfigError("calendar.reminders_minutes は 5 件までです（Google カレンダーの制限）")

        if self.calendar.duration_minutes < 0:
            raise ConfigError("calendar.duration_minutes は 0 以上にしてください")

        if self.imap.enabled and not (self.imap.host and self.imap.username):
            raise ConfigError("imap を使うには imap.host と imap.username が必要です")

        if not self.gmail.enabled and not self.imap.enabled:
            raise ConfigError("取り込み元がありません（gmail.enabled か imap.enabled を true に）")


def _read_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise ConfigError("設定ファイルの読み込みには PyYAML が必要です: pip install PyYAML")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"設定ファイルを解釈できません ({path}): {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"設定ファイルの最上位はマッピングである必要があります: {path}")
    return data


def _merge(target: Any, data: dict[str, Any], path: str) -> Any:
    """dataclass の既定値に、YAML の値を再帰的に上書きする。"""
    known = {f.name: f for f in dataclasses.fields(target)}
    for key, value in data.items():
        full_key = f"{path}{key}"
        if key not in known:
            raise ConfigError(
                f"設定に未知の項目があります: {full_key}（使える項目: {', '.join(sorted(known))}）"
            )
        current = getattr(target, key)
        if dataclasses.is_dataclass(current):
            if not isinstance(value, dict):
                raise ConfigError(f"{full_key} はマッピングで指定してください")
            _merge(current, value, path=f"{full_key}.")
            continue
        if isinstance(current, bool) and not isinstance(value, bool):
            raise ConfigError(f"{full_key} は true / false で指定してください")
        if isinstance(current, list) and not isinstance(value, list):
            raise ConfigError(f"{full_key} はリストで指定してください")
        setattr(target, key, value)
    return target


# `subcal init-config` が書き出すひな形。config.example.yaml と同じ内容
# （tests/test_config.py で一致を確認している）。
EXAMPLE_CONFIG = """\
# submission-calendar 設定ファイル
# 変えたい項目だけ書けば、残りは既定値が使われます。

# 締め切りの解釈に使うタイムゾーン
timezone: Asia/Tokyo

# 抽出方法
#   rules … キーワードと正規表現だけで判定する（無料・既定）
#   llm   … Claude に判定させる（要 ANTHROPIC_API_KEY）
#   auto  … rules で取りこぼしたメールだけ Claude に回す
extractor: rules

# Google Cloud で作成した OAuth クライアント情報（デスクトップアプリ）
credentials_file: credentials.json
token_file: ~/.config/submission-calendar/token.json
state_file: ~/.config/submission-calendar/state.json

gmail:
  enabled: true
  # Gmail の検索クエリ。ラベルや差出人で絞り込むと精度が上がります
  #   例) newer_than:60d (label:大学 OR from:example.ac.jp)
  query: "newer_than:60d -category:promotions -category:social"
  # 上のクエリに「提出・課題・締切…」などのキーワード条件を自動で足す
  add_keyword_filter: true
  max_results: 50
  # 処理済みメールに付けるラベル（空なら付けない。gmail.modify 権限が必要）
  label_processed: ""

# Outlook / Microsoft 365 や大学のメールサーバから IMAP で取り込む場合
imap:
  enabled: false
  host: outlook.office365.com   # 大学のサーバなら imap.example.ac.jp など
  port: 993
  use_ssl: true
  username: ""                  # 大学のメールアドレス
  # パスワードは設定ファイルに書かず、この環境変数から読みます
  #   export SUBCAL_IMAP_PASSWORD='...'
  password_env: SUBCAL_IMAP_PASSWORD
  mailbox: INBOX
  days: 60
  max_results: 50

calendar:
  # 書き込み先。専用カレンダーを作ってその ID を入れるのがおすすめ
  calendar_id: primary
  event_prefix: "[提出] "
  # 時刻が書かれていなかったときに終日予定にするか
  all_day_when_time_unknown: false
  # 上が false のときに使う締め切り時刻
  default_due_time: "23:59"
  # 締め切りの何分前から予定を入れるか
  duration_minutes: 30
  # 通知（分前）。1440 = 1日前、180 = 3時間前
  reminders_minutes: [1440, 180]
  # 予定の色 ID（"1"〜"11"。空なら既定色）
  color_id: ""

detection:
  # 提出依頼と判定するスコアのしきい値（件名に出てきた語は 2 倍で数えます）
  min_score: 3.0
  # 自分の用途に合わせて足したいキーワード
  extra_keywords: []
  # 件名・差出人にこれらが含まれていたら対象外にする
  exclude_keywords:
    - メールマガジン
    - メルマガ
    - セール
    - キャンペーン
    - 広告
    - newsletter
    - unsubscribe
  exclude_senders: []
  # 受信日より何日以上前の日付を締め切り候補から外すか
  ignore_past_days: 1

llm:
  model: claude-opus-5
  effort: low
  max_tokens: 2048
"""
