"""IMAP でメールを取り込む（Outlook / Microsoft 365、大学のメールサーバなど）。

標準ライブラリの imaplib だけで動く。Gmail 取り込みと同じ Message を返すので、
締め切りの抽出とカレンダー登録の処理はそのまま共有できる。
"""

from __future__ import annotations

import email
import hashlib
import imaplib
import re
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.message import Message as EmailMessage
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from ..models import Message
from .gmail import html_to_text

__all__ = ["ImapSource", "decode_mime_header", "body_from_email", "message_from_email",
           "imap_date", "OUTLOOK_HOST"]

OUTLOOK_HOST = "outlook.office365.com"

# ロケールに左右されないよう、IMAP の日付は自前で組み立てる
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# 本文の文字コードは宣言が正しくないことがあるので、順に試す
_FALLBACK_CHARSETS = ("utf-8", "iso-2022-jp", "cp932", "euc-jp", "latin-1")

_ANGLE_BRACKETS = re.compile(r"^<|>$")


def imap_date(value: date) -> str:
    """IMAP の SEARCH に渡す日付文字列（例: 01-Sep-2026）。"""
    return f"{value.day:02d}-{_MONTHS[value.month - 1]}-{value.year}"


def decode_mime_header(value: str | None) -> str:
    """MIME エンコードされたヘッダ（=?utf-8?B?...?=）を読める文字列にする。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError, ValueError):
        return value


def _decode_payload(payload: bytes, charset: str | None) -> str:
    candidates = [charset] if charset else []
    candidates.extend(name for name in _FALLBACK_CHARSETS if name != charset)
    for name in candidates:
        try:
            return payload.decode(name)
        except (UnicodeDecodeError, LookupError):
            continue
    return payload.decode("utf-8", errors="replace")


def body_from_email(mail: EmailMessage) -> str:
    """メールの本文を取り出す。text/plain を優先し、無ければ HTML から作る。"""
    plain: list[str] = []
    rich: list[str] = []

    for part in mail.walk() if mail.is_multipart() else [mail]:
        content_type = part.get_content_type()
        if content_type not in ("text/plain", "text/html"):
            continue
        if "attachment" in (part.get("Content-Disposition") or "").lower():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        text = _decode_payload(payload, part.get_content_charset())
        (plain if content_type == "text/plain" else rich).append(text)

    if any(chunk.strip() for chunk in plain):
        return "\n".join(plain).strip()
    return html_to_text("\n".join(rich)).strip()


def _stable_id(mail: EmailMessage) -> str:
    """実行のたびに変わらない ID。Message-ID があればそれを使う。"""
    message_id = _ANGLE_BRACKETS.sub("", (mail.get("Message-ID") or "").strip())
    if message_id:
        return message_id
    seed = f"{mail.get('Subject', '')}|{mail.get('From', '')}|{mail.get('Date', '')}"
    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:24]


def message_from_email(mail: EmailMessage, tz: ZoneInfo) -> Message:
    received = datetime.now(tz)
    if raw_date := mail.get("Date"):
        try:
            parsed = parsedate_to_datetime(raw_date)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=tz)
                received = parsed.astimezone(tz)
        except (TypeError, ValueError):
            pass

    return Message(
        id=_stable_id(mail),
        subject=decode_mime_header(mail.get("Subject")),
        sender=decode_mime_header(mail.get("From")),
        received_at=received,
        body=body_from_email(mail),
        source="imap",
    )


class ImapSource:
    name = "imap"

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 993,
        mailbox: str = "INBOX",
        use_ssl: bool = True,
        timezone_name: str = "Asia/Tokyo",
        connection=None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.mailbox = mailbox
        self.use_ssl = use_ssl
        self.tz = ZoneInfo(timezone_name)
        self._connection = connection  # テストで差し替えるため

    def _connect(self):
        if self._connection is not None:
            return self._connection
        factory = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        connection = factory(self.host, self.port)
        try:
            connection.login(self.username, self.password)
        except imaplib.IMAP4.error as exc:
            raise RuntimeError(
                f"IMAP にログインできませんでした ({self.host}): {exc}\n"
                "パスワードが正しいか、サーバ側で IMAP が有効か確認してください。"
                "Microsoft 365 でアプリパスワードが必要な場合もあります。"
            ) from exc
        return connection

    def search(self, days: int = 60, max_results: int = 50) -> list[Message]:
        """直近 days 日のメールを新しい順に最大 max_results 件取得する。"""
        connection = self._connect()
        try:
            connection.select(self.mailbox, readonly=True)
            since = imap_date((datetime.now(self.tz) - timedelta(days=days)).date())
            status, data = connection.uid("SEARCH", None, "SINCE", since)
            if status != "OK":
                raise RuntimeError(f"IMAP の検索に失敗しました: {status}")

            uids = (data[0] or b"").split()
            messages: list[Message] = []
            for uid in reversed(uids[-max_results:] if max_results else uids):
                status, payload = connection.uid("FETCH", uid, "(RFC822)")
                if status != "OK" or not payload:
                    continue
                raw = next(
                    (item[1] for item in payload if isinstance(item, tuple) and len(item) > 1),
                    None,
                )
                if not raw:
                    continue
                messages.append(message_from_email(email.message_from_bytes(raw), self.tz))
            return messages
        finally:
            if self._connection is None:
                try:
                    connection.logout()
                except Exception:  # 後片付けの失敗は無視してよい
                    pass
