"""Gmail からメッセージを取り込む。

Google のクライアントライブラリに依存するのは service オブジェクトの受け渡しだけで、
本文の取り出しなどの処理は純粋な関数として書いてある（テストしやすくするため）。
"""

from __future__ import annotations

import base64
import html
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from ..models import Message

__all__ = ["GmailSource", "build_query", "extract_body", "header_value", "KEYWORD_QUERY"]

# 提出依頼を含みそうなメールだけに絞るための Gmail 検索条件
KEYWORD_QUERY = (
    "(提出 OR 課題 OR レポート OR 締切 OR 〆切 OR 期限 OR 必着 OR 申込 OR 応募 "
    'OR deadline OR submission OR assignment OR "due date")'
)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BREAK_RE = re.compile(r"<br\s*/?>|</p>|</div>|</tr>|</li>|</h[1-6]>", re.IGNORECASE)
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def build_query(base_query: str, add_keyword_filter: bool = True) -> str:
    """設定のクエリにキーワード条件を足す。"""
    parts = [part for part in (base_query.strip(), KEYWORD_QUERY if add_keyword_filter else "") if part]
    return " ".join(parts)


def header_value(payload: dict, name: str) -> str:
    for item in payload.get("headers", []) or []:
        if item.get("name", "").lower() == name.lower():
            return item.get("value", "")
    return ""


def _decode(data: str | None) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError):
        return ""
    return raw.decode("utf-8", errors="replace")


def html_to_text(markup: str) -> str:
    text = _SCRIPT_RE.sub(" ", markup)
    text = _BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


def extract_body(payload: dict) -> str:
    """MIME ツリーをたどって本文を取り出す。text/plain を優先する。"""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {}) or {}
        if part.get("parts"):
            for child in part["parts"]:
                walk(child)
            return
        if mime == "text/plain":
            plain.append(_decode(body.get("data")))
        elif mime == "text/html":
            rich.append(html_to_text(_decode(body.get("data"))))

    walk(payload)
    text = "\n".join(chunk for chunk in plain if chunk).strip()
    if not text:
        text = "\n".join(chunk for chunk in rich if chunk).strip()
    return text


def parse_received_at(payload: dict, internal_date: str | None, tz: ZoneInfo) -> datetime:
    """受信日時を求める。Date ヘッダを優先し、駄目なら internalDate を使う。"""
    raw = header_value(payload, "Date")
    if raw:
        try:
            parsed = parsedate_to_datetime(raw)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=tz)
                return parsed.astimezone(tz)
        except (TypeError, ValueError):
            pass
    if internal_date:
        try:
            epoch_ms = int(internal_date)
        except (TypeError, ValueError):
            epoch_ms = 0
        if epoch_ms:
            return datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(tz)
    return datetime.now(tz)


def to_message(raw: dict, tz: ZoneInfo) -> Message:
    """Gmail API の messages.get のレスポンスを Message に変換する。"""
    payload = raw.get("payload", {}) or {}
    body = extract_body(payload) or raw.get("snippet", "")
    message_id = raw.get("id", "")
    return Message(
        id=message_id,
        subject=header_value(payload, "Subject"),
        sender=header_value(payload, "From"),
        received_at=parse_received_at(payload, raw.get("internalDate"), tz),
        body=body,
        source="gmail",
        url=f"https://mail.google.com/mail/u/0/#all/{message_id}" if message_id else "",
        thread_id=raw.get("threadId", ""),
    )


class GmailSource:
    name = "gmail"

    def __init__(self, service, timezone_name: str = "Asia/Tokyo", user_id: str = "me"):
        self.service = service
        self.user_id = user_id
        self.tz = ZoneInfo(timezone_name)

    def search(self, query: str, max_results: int = 50) -> list[Message]:
        """クエリに一致したメールを新しい順に取得する。"""
        ids: list[str] = []
        page_token: str | None = None
        while len(ids) < max_results:
            response = (
                self.service.users()
                .messages()
                .list(
                    userId=self.user_id,
                    q=query,
                    maxResults=min(100, max_results - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            ids.extend(item["id"] for item in response.get("messages", []) or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        messages: list[Message] = []
        for message_id in ids[:max_results]:
            raw = (
                self.service.users()
                .messages()
                .get(userId=self.user_id, id=message_id, format="full")
                .execute()
            )
            messages.append(to_message(raw, self.tz))
        return messages

    # --- ラベル ------------------------------------------------------
    def ensure_label(self, name: str) -> str:
        """ラベルを（無ければ作って）ID を返す。"""
        response = self.service.users().labels().list(userId=self.user_id).execute()
        for label in response.get("labels", []) or []:
            if label.get("name") == name:
                return label["id"]
        created = (
            self.service.users()
            .labels()
            .create(
                userId=self.user_id,
                body={
                    "name": name,
                    "labelListVisibility": "labelShow",
                    "messageListVisibility": "show",
                },
            )
            .execute()
        )
        return created["id"]

    def add_label(self, message_id: str, label_id: str) -> None:
        self.service.users().messages().modify(
            userId=self.user_id, id=message_id, body={"addLabelIds": [label_id]}
        ).execute()
