"""テスト用の偽 Google API サービス。

google-api-python-client のメソッドチェーン
（service.users().messages().list(...).execute()）と同じ形だけを真似ている。
"""

from __future__ import annotations

import base64
from datetime import datetime
from email.utils import format_datetime


class _Call:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


def raw_message(
    message_id: str,
    subject: str,
    body: str,
    sender: str = "prof@example.ac.jp",
    received: datetime | None = None,
    mime_type: str = "text/plain",
) -> dict:
    received = received or datetime(2026, 9, 11, 9, 0)
    encoded = base64.urlsafe_b64encode(body.encode("utf-8")).decode().rstrip("=")
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "internalDate": str(int(received.timestamp() * 1000)),
        "snippet": body[:80],
        "payload": {
            "mimeType": mime_type,
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "Date", "value": format_datetime(received)},
            ],
            "body": {"data": encoded},
        },
    }


class FakeGmailMessages:
    def __init__(self, messages: list[dict]):
        self.messages = messages
        self.modified: list[tuple[str, dict]] = []

    def list(self, userId, q, maxResults, pageToken=None):
        self.last_query = q
        items = [{"id": m["id"]} for m in self.messages][:maxResults]
        return _Call({"messages": items})

    def get(self, userId, id, format):
        return _Call(next(m for m in self.messages if m["id"] == id))

    def modify(self, userId, id, body):
        self.modified.append((id, body))
        return _Call({"id": id})


class FakeGmailLabels:
    def __init__(self):
        self.labels: list[dict] = []

    def list(self, userId):
        return _Call({"labels": list(self.labels)})

    def create(self, userId, body):
        label = {"id": f"Label_{len(self.labels) + 1}", **body}
        self.labels.append(label)
        return _Call(label)


class FakeGmailService:
    def __init__(self, messages: list[dict]):
        self._messages = FakeGmailMessages(messages)
        self._labels = FakeGmailLabels()

    def users(self):
        return self

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels


class FakeEvents:
    def __init__(self):
        self.store: dict[str, dict] = {}
        self.inserts = 0
        self.patches = 0
        self._counter = 0

    def list(self, calendarId, privateExtendedProperty=None, **kwargs):
        items = list(self.store.values())
        if privateExtendedProperty:
            key, _, value = privateExtendedProperty.partition("=")
            items = [
                event
                for event in items
                if (event.get("extendedProperties", {}).get("private", {}) or {}).get(key) == value
            ]
        return _Call({"items": items})

    def insert(self, calendarId, body):
        self._counter += 1
        self.inserts += 1
        event_id = f"evt{self._counter}"
        event = {**body, "id": event_id, "status": "confirmed",
                 "htmlLink": f"https://calendar.google.com/event?eid={event_id}"}
        self.store[event_id] = event
        return _Call(event)

    def patch(self, calendarId, eventId, body):
        self.patches += 1
        self.store[eventId] = {**self.store[eventId], **body}
        return _Call(self.store[eventId])


class FakeCalendarService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events


class FakeImapSource:
    """ImapSource の search() だけを真似たもの。"""

    def __init__(self, mails: list[tuple[str, str]], received: datetime | None = None):
        self.mails = mails
        self.received = received or datetime(2026, 9, 11, 9, 0)
        self.calls: list[tuple[int, int]] = []

    def search(self, days: int = 60, max_results: int = 50):
        from submission_calendar.models import Message

        self.calls.append((days, max_results))
        return [
            Message(
                id=f"imap-{index}@example.ac.jp",
                subject=subject,
                sender="kyomu@example.ac.jp",
                received_at=self.received,
                body=body,
                source="imap",
            )
            for index, (subject, body) in enumerate(self.mails[:max_results])
        ]
