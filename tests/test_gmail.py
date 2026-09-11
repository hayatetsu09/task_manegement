import base64
from datetime import datetime
from zoneinfo import ZoneInfo

from fakes import FakeGmailService, raw_message

from submission_calendar.sources.gmail import (
    GmailSource,
    build_query,
    extract_body,
    header_value,
    html_to_text,
    parse_received_at,
    to_message,
)

TOKYO = ZoneInfo("Asia/Tokyo")


def encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def test_plain_text_is_preferred_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": encode("本文プレーン")}},
            {"mimeType": "text/html", "body": {"data": encode("<p>本文HTML</p>")}},
        ],
    }
    assert extract_body(payload) == "本文プレーン"


def test_html_is_used_when_there_is_no_plain_text():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [{"mimeType": "text/html", "body": {"data": encode("<p>提出期限は9/20</p>")}}],
    }
    assert extract_body(payload) == "提出期限は9/20"


def test_nested_multipart_is_walked():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": encode("入れ子の本文")}}],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "a1"}},
        ],
    }
    assert extract_body(payload) == "入れ子の本文"


def test_html_to_text_drops_scripts_and_keeps_line_breaks():
    assert html_to_text("<style>p{}</style><p>1行目</p>2行目&nbsp;&amp;") == "1行目\n2行目 &"


def test_header_lookup_is_case_insensitive():
    payload = {"headers": [{"name": "subject", "value": "件名"}]}
    assert header_value(payload, "Subject") == "件名"


def test_received_at_uses_the_date_header():
    payload = {"headers": [{"name": "Date", "value": "Fri, 11 Sep 2026 09:30:00 +0900"}]}
    received = parse_received_at(payload, None, TOKYO)
    assert (received.year, received.month, received.day, received.hour) == (2026, 9, 11, 9)


def test_received_at_falls_back_to_internal_date():
    epoch_ms = int(datetime(2026, 9, 11, 0, 0, tzinfo=TOKYO).timestamp() * 1000)
    received = parse_received_at({"headers": []}, str(epoch_ms), TOKYO)
    assert received.date() == datetime(2026, 9, 11).date()


def test_to_message_builds_a_gmail_permalink():
    message = to_message(raw_message("abc123", "件名", "本文"), TOKYO)
    assert message.source_id == "gmail:abc123"
    assert message.url.endswith("#all/abc123")


def test_search_returns_messages_in_order():
    service = FakeGmailService([raw_message("m1", "件名1", "本文1"), raw_message("m2", "件名2", "本文2")])
    messages = GmailSource(service).search("q", max_results=10)
    assert [m.subject for m in messages] == ["件名1", "件名2"]


def test_search_respects_the_limit():
    service = FakeGmailService([raw_message(f"m{i}", f"件名{i}", "本文") for i in range(5)])
    assert len(GmailSource(service).search("q", max_results=2)) == 2


def test_ensure_label_creates_once():
    service = FakeGmailService([])
    source = GmailSource(service)
    first = source.ensure_label("提出物/登録済み")
    second = source.ensure_label("提出物/登録済み")
    assert first == second
    assert len(service.labels().labels) == 1


def test_build_query_appends_keywords():
    assert build_query("newer_than:30d").startswith("newer_than:30d (提出 OR")
    assert build_query("newer_than:30d", add_keyword_filter=False) == "newer_than:30d"
