"""取り込み元と出力を JSON でつなぐための変換。

Google API を直接叩かない経路（Claude の Gmail / Google カレンダー連携など）から
締め切り抽出だけを再利用できるようにする。
"""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from .calendar_sync import CalendarSync, marker_for
from .config import Config
from .models import Message, Submission

__all__ = ["message_from_dict", "submission_to_dict", "parse_datetime"]


def parse_datetime(value, tz: ZoneInfo) -> datetime:
    """ISO 8601 でも RFC 2822（メールの Date ヘッダ）でも受け取れるようにする。"""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):  # epoch 秒
        parsed = datetime.fromtimestamp(value, tz=tz)
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"受信日時を解釈できません: {value!r}") from exc
    else:
        parsed = datetime.now(tz)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def message_from_dict(data: dict, tz: ZoneInfo) -> Message:
    """JSON のメール 1 通を Message に変換する。

    キー名は取り込み元によって揺れるので、よくある別名も受け付ける。
    """
    message_id = str(data.get("id") or data.get("messageId") or data.get("message_id") or "")
    if not message_id:
        raise ValueError("メールに id がありません")
    received = data.get("received_at") or data.get("receivedAt") or data.get("date")
    return Message(
        id=message_id,
        subject=data.get("subject") or "",
        sender=data.get("sender") or data.get("from") or "",
        received_at=parse_datetime(received, tz),
        body=data.get("body") or data.get("text") or data.get("snippet") or "",
        source=data.get("source") or "gmail",
        url=data.get("url") or data.get("link") or "",
        thread_id=str(data.get("thread_id") or data.get("threadId") or ""),
    )


def submission_to_dict(submission: Submission, config: Config) -> dict:
    """提出依頼を、カレンダー登録にそのまま使える形にして返す。"""
    tz = ZoneInfo(config.timezone)
    message = submission.message
    result: dict = {
        "source_id": submission.source_id,
        "marker": marker_for(submission.source_id),
        "search_key": message.id,
        "title": submission.title,
        "subject": message.subject,
        "sender": message.sender,
        "received_at": message.received_at.isoformat(),
        "url": message.url,
        "score": submission.score,
        "extractor": submission.extractor,
        "notes": list(submission.notes),
        "due_date": None,
        "due_time": None,
        "deadline_text": "",
        "confidence": 0.0,
        "event": None,
    }
    if submission.deadline is None:
        return result

    deadline = submission.deadline
    result["due_date"] = deadline.date.isoformat()
    result["due_time"] = deadline.time.strftime("%H:%M") if deadline.time else None
    result["deadline_text"] = deadline.text
    result["confidence"] = deadline.confidence

    # カレンダー API にも Claude の連携にもそのまま渡せる形にしておく
    body = CalendarSync(None, config).build_event(submission)
    all_day = "date" in body["start"]
    if all_day:
        start_time = f"{body['start']['date']}T00:00:00"
        end_time = f"{body['end']['date']}T00:00:00"
    else:
        start_time = datetime.fromisoformat(body["start"]["dateTime"]).replace(tzinfo=tz).isoformat()
        end_time = datetime.fromisoformat(body["end"]["dateTime"]).replace(tzinfo=tz).isoformat()

    result["event"] = {
        "summary": body["summary"],
        "description": body["description"],
        "startTime": start_time,
        "endTime": end_time,
        "allDay": all_day,
        "timeZone": config.timezone,
        "calendarId": config.calendar.calendar_id,
        "overrideReminders": [
            {"method": reminder["method"], "minutes": reminder["minutes"]}
            for reminder in body["reminders"].get("overrides", [])
        ],
    }
    if body.get("colorId"):
        result["event"]["colorId"] = body["colorId"]
    return result
