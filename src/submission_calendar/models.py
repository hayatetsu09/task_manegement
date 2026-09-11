"""ツール全体で使うデータ構造。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

__all__ = ["Message", "Deadline", "Submission"]


@dataclass
class Message:
    """取り込み元（Gmail など）から取得した 1 通のメッセージ。"""

    id: str
    subject: str
    sender: str
    received_at: datetime
    body: str
    source: str = "gmail"
    url: str = ""
    thread_id: str = ""

    @property
    def source_id(self) -> str:
        """取り込み元をまたいで一意になる ID。カレンダー予定の重複判定に使う。"""
        return f"{self.source}:{self.id}"

    @property
    def searchable_text(self) -> str:
        return f"{self.subject}\n{self.body}"


@dataclass(frozen=True)
class Deadline:
    """抽出できた締め切り。"""

    date: date
    time: time | None = None
    text: str = ""
    confidence: float = 0.5

    @property
    def has_time(self) -> bool:
        return self.time is not None

    def as_datetime(self, default_time: time) -> datetime:
        return datetime.combine(self.date, self.time or default_time)

    def describe(self) -> str:
        if self.time is None:
            return self.date.strftime("%Y-%m-%d")
        return f"{self.date:%Y-%m-%d} {self.time:%H:%M}"


@dataclass
class Submission:
    """提出依頼 1 件。カレンダーの予定 1 件に対応する。"""

    message: Message
    title: str
    deadline: Deadline | None = None
    score: float = 0.0
    extractor: str = "rules"
    notes: list[str] = field(default_factory=list)

    @property
    def has_deadline(self) -> bool:
        return self.deadline is not None

    @property
    def source_id(self) -> str:
        return self.message.source_id
