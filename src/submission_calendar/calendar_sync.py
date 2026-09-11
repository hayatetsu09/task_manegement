"""抽出した提出依頼を Google カレンダーの予定に反映する。

同じメールから何度実行しても予定が増えないよう、予定の extendedProperties に
元メールの ID を埋め込んでおき、実行のたびにそれを検索して作成/更新を切り替える。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Config
from .dates import parse_time_string
from .models import Submission

__all__ = ["CalendarSync", "SyncResult", "build_description", "marker_for",
           "EXT_SOURCE_KEY", "EXT_FP_KEY"]

# 予定に埋め込む目印（Google カレンダーの private extended property）
EXT_SOURCE_KEY = "subcal_source"
EXT_FP_KEY = "subcal_fp"

# 予定の説明文に埋め込む目印。カレンダーを検索して重複を防ぐために使う
# （extendedProperties を読み書きできない経路——Claude の連携など——のための手段）。
MARKER_PREFIX = "subcal"


def marker_for(source_id: str) -> str:
    return f"{MARKER_PREFIX}:{source_id}"


def build_description(submission: Submission) -> str:
    """予定の説明文。元メールへの導線と、重複防止の目印を含む。"""
    message = submission.message
    lines = [
        f"件名: {message.subject}",
        f"差出人: {message.sender}",
        f"受信: {message.received_at:%Y-%m-%d %H:%M}",
    ]
    if submission.deadline and submission.deadline.text:
        lines.append(f"メール中の締め切り表記: 「{submission.deadline.text}」")
    if message.url:
        lines.append(f"元のメール: {message.url}")
    if submission.notes:
        lines.append("")
        lines.extend(f"※ {note}" for note in submission.notes)
    lines.append("")
    lines.append(f"[{marker_for(submission.source_id)}] submission-calendar が "
                 f"{submission.extractor} で自動作成")
    return "\n".join(lines)


ACTION_LABELS = {
    "created": "作成",
    "updated": "更新",
    "unchanged": "変更なし",
    "skipped": "スキップ",
    "failed": "失敗",
}


@dataclass
class SyncResult:
    action: str  # created / updated / unchanged / skipped / failed
    submission: Submission
    event_id: str = ""
    fingerprint: str = ""
    detail: str = ""
    html_link: str = ""

    @property
    def label(self) -> str:
        return ACTION_LABELS.get(self.action, self.action)

    @property
    def ok(self) -> bool:
        return self.action in ("created", "updated", "unchanged")


class CalendarSync:
    def __init__(self, service, config: Config):
        self.service = service
        self.config = config
        self.calendar_id = config.calendar.calendar_id
        self.tz = ZoneInfo(config.timezone)
        self.default_due_time = parse_time_string(config.calendar.default_due_time)

    # --- 予定の組み立て -----------------------------------------------
    def fingerprint(self, submission: Submission) -> str:
        """タイトルと締め切りから作る指紋。変化したときだけ予定を更新するために使う。"""
        deadline = submission.deadline
        parts = [
            submission.title,
            deadline.date.isoformat() if deadline else "",
            deadline.time.isoformat() if deadline and deadline.time else "",
            self.config.calendar.event_prefix,
            "allday" if self._is_all_day(submission) else "timed",
        ]
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]

    def _is_all_day(self, submission: Submission) -> bool:
        deadline = submission.deadline
        return bool(
            deadline
            and deadline.time is None
            and self.config.calendar.all_day_when_time_unknown
        )

    def _times(self, submission: Submission) -> tuple[dict, dict]:
        deadline = submission.deadline
        assert deadline is not None  # 呼び出し側で締め切りの有無を確認済み
        if self._is_all_day(submission):
            return (
                {"date": deadline.date.isoformat()},
                {"date": (deadline.date + timedelta(days=1)).isoformat()},
            )
        end = datetime.combine(deadline.date, deadline.time or self.default_due_time)
        start = end - timedelta(minutes=self.config.calendar.duration_minutes)
        return (
            {"dateTime": start.isoformat(), "timeZone": self.config.timezone},
            {"dateTime": end.isoformat(), "timeZone": self.config.timezone},
        )

    def build_event(self, submission: Submission) -> dict:
        start, end = self._times(submission)
        calendar_config = self.config.calendar
        event: dict = {
            "summary": f"{calendar_config.event_prefix}{submission.title}",
            "description": build_description(submission),
            "start": start,
            "end": end,
            "extendedProperties": {
                "private": {
                    EXT_SOURCE_KEY: submission.source_id,
                    EXT_FP_KEY: self.fingerprint(submission),
                }
            },
        }
        reminders = [
            {"method": "popup", "minutes": int(minutes)}
            for minutes in calendar_config.reminders_minutes[:5]
        ]
        event["reminders"] = {"useDefault": not reminders, "overrides": reminders}
        if calendar_config.color_id:
            event["colorId"] = str(calendar_config.color_id)
        if submission.message.url.startswith("http"):
            event["source"] = {"title": "元のメール", "url": submission.message.url}
        return event

    # --- Google カレンダーとのやりとり ---------------------------------
    def find_existing(self, source_id: str) -> dict | None:
        """同じメールから作った予定を探す。"""
        response = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                privateExtendedProperty=f"{EXT_SOURCE_KEY}={source_id}",
                showDeleted=False,
                singleEvents=True,
                maxResults=5,
            )
            .execute()
        )
        for item in response.get("items", []) or []:
            if item.get("status") != "cancelled":
                return item
        return None

    def sync(
        self,
        submission: Submission,
        *,
        dry_run: bool = False,
        known_fingerprint: str | None = None,
    ) -> SyncResult:
        """提出依頼 1 件をカレンダーに反映する。

        known_fingerprint に前回と同じ値が渡された場合は API を呼ばずに終える。
        """
        if not submission.has_deadline:
            return SyncResult("skipped", submission, detail="締め切りが不明")

        fingerprint = self.fingerprint(submission)
        if known_fingerprint == fingerprint:
            return SyncResult("unchanged", submission, fingerprint=fingerprint,
                              detail="前回の実行から変化なし")

        event = self.build_event(submission)
        try:
            existing = self.find_existing(submission.source_id)
            if existing is None:
                if dry_run:
                    return SyncResult("created", submission, fingerprint=fingerprint,
                                      detail="(dry-run)")
                created = (
                    self.service.events()
                    .insert(calendarId=self.calendar_id, body=event)
                    .execute()
                )
                return SyncResult("created", submission, event_id=created.get("id", ""),
                                  fingerprint=fingerprint, html_link=created.get("htmlLink", ""))

            current = (existing.get("extendedProperties", {}).get("private", {}) or {}).get(EXT_FP_KEY)
            if current == fingerprint:
                return SyncResult("unchanged", submission, event_id=existing.get("id", ""),
                                  fingerprint=fingerprint, html_link=existing.get("htmlLink", ""))

            if dry_run:
                return SyncResult("updated", submission, event_id=existing.get("id", ""),
                                  fingerprint=fingerprint, detail="(dry-run)")
            updated = (
                self.service.events()
                .patch(calendarId=self.calendar_id, eventId=existing["id"], body=event)
                .execute()
            )
            return SyncResult("updated", submission, event_id=updated.get("id", ""),
                              fingerprint=fingerprint, html_link=updated.get("htmlLink", ""))
        except Exception as exc:  # API エラーで全体を止めない
            return SyncResult("failed", submission, fingerprint=fingerprint,
                              detail=f"{type(exc).__name__}: {exc}")
