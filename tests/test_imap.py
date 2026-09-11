"""IMAP 取り込みのテスト。ネットワークには接続せず、偽の接続を使う。"""

import email
from datetime import date, datetime
from email.message import EmailMessage
from zoneinfo import ZoneInfo

import pytest

from submission_calendar.sources.imap import (
    ImapSource,
    body_from_email,
    decode_mime_header,
    imap_date,
    message_from_email,
)

TOKYO = ZoneInfo("Asia/Tokyo")


def build_mail(subject="レポート提出のお願い", body="提出期限は9/20です",
               charset="utf-8", message_id="<abc123@example.ac.jp>", html=None):
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = "教務課 <kyomu@example.ac.jp>"
    mail["Date"] = "Fri, 11 Sep 2026 09:00:00 +0900"
    if message_id:
        mail["Message-ID"] = message_id
    if html is not None:
        mail.set_content(body, charset=charset)
        mail.add_alternative(html, subtype="html", charset=charset)
    else:
        mail.set_content(body, charset=charset)
    return email.message_from_bytes(mail.as_bytes())


def test_subject_and_body_are_decoded():
    message = message_from_email(build_mail(), TOKYO)
    assert message.subject == "レポート提出のお願い"
    assert message.body.strip() == "提出期限は9/20です"
    assert message.sender.startswith("教務課")


@pytest.mark.parametrize("charset", ["utf-8", "iso-2022-jp", "euc-jp", "shift_jis"])
def test_japanese_charsets_are_decoded(charset):
    """大学のメールは ISO-2022-JP などで届くことがある。"""
    message = message_from_email(build_mail(charset=charset), TOKYO)
    assert message.body.strip() == "提出期限は9/20です"


def test_mislabelled_charset_still_decodes():
    mail = build_mail()
    del mail["Content-Type"]
    mail["Content-Type"] = 'text/plain; charset="x-unknown"'
    assert "提出期限" in body_from_email(mail)


def test_html_only_mail_is_converted_to_text():
    mail = EmailMessage()
    mail["Subject"] = "課題"
    mail["Date"] = "Fri, 11 Sep 2026 09:00:00 +0900"
    mail.set_content("<p>提出期限は9/20です</p>", subtype="html", charset="utf-8")
    assert body_from_email(email.message_from_bytes(mail.as_bytes())) == "提出期限は9/20です"


def test_plain_text_is_preferred_over_html():
    mail = build_mail(body="プレーン本文", html="<p>HTML本文</p>")
    assert body_from_email(mail).strip() == "プレーン本文"


def test_received_at_uses_the_date_header():
    message = message_from_email(build_mail(), TOKYO)
    assert message.received_at.hour == 9
    assert message.received_at.date() == date(2026, 9, 11)


def test_message_id_becomes_a_stable_id():
    message = message_from_email(build_mail(), TOKYO)
    assert message.id == "abc123@example.ac.jp"
    assert message.source_id == "imap:abc123@example.ac.jp"


def test_id_is_derived_when_there_is_no_message_id():
    first = message_from_email(build_mail(message_id=None), TOKYO)
    second = message_from_email(build_mail(message_id=None), TOKYO)
    assert first.id == second.id  # 実行のたびに変わらない
    assert first.id != message_from_email(build_mail(subject="別件", message_id=None), TOKYO).id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("=?utf-8?B?44Os44Od44O844OI?=", "レポート"),
        ("plain subject", "plain subject"),
        (None, ""),
    ],
)
def test_decode_mime_header(raw, expected):
    assert decode_mime_header(raw) == expected


def test_imap_date_is_locale_independent():
    assert imap_date(date(2026, 9, 1)) == "01-Sep-2026"


class FakeConnection:
    """imaplib.IMAP4_SSL のうち、使っている部分だけを真似たもの。"""

    def __init__(self, mails):
        self.mails = {str(index + 1).encode(): mail for index, mail in enumerate(mails)}
        self.selected = None
        self.searches = []

    def select(self, mailbox, readonly=False):
        self.selected = (mailbox, readonly)
        return ("OK", [b""])

    def uid(self, command, *args):
        if command == "SEARCH":
            self.searches.append(args)
            return ("OK", [b" ".join(self.mails)])
        if command == "FETCH":
            uid = args[0]
            return ("OK", [(b"1 (RFC822 {})", self.mails[uid])])
        raise AssertionError(command)


def source_with(mails, **kwargs):
    connection = FakeConnection(mails)
    source = ImapSource("outlook.office365.com", "me@example.ac.jp", "pw",
                        connection=connection, **kwargs)
    return source, connection


def test_search_returns_newest_first():
    mails = [build_mail(subject=f"件名{i}", message_id=f"<m{i}@x>").as_bytes() for i in range(3)]
    source, connection = source_with(mails)
    messages = source.search(days=30, max_results=10)
    assert [m.subject for m in messages] == ["件名2", "件名1", "件名0"]
    assert connection.selected == ("INBOX", True)  # 読み取り専用で開く


def test_search_limits_the_number_of_mails():
    mails = [build_mail(message_id=f"<m{i}@x>").as_bytes() for i in range(5)]
    source, _ = source_with(mails)
    assert len(source.search(max_results=2)) == 2


def test_search_uses_a_since_filter():
    source, connection = source_with([build_mail().as_bytes()])
    source.search(days=30)
    assert connection.searches[0][1] == "SINCE"


def test_mailbox_can_be_changed():
    source, connection = source_with([build_mail().as_bytes()], mailbox="受信トレイ")
    source.search()
    assert connection.selected[0] == "受信トレイ"
