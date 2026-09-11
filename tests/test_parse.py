"""`subcal parse`（JSON 入出力）のテスト。Google 認証なしで動くことを確かめる。"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from submission_calendar import cli
from submission_calendar.config import Config
from submission_calendar.serialize import message_from_dict, parse_datetime, submission_to_dict

TOKYO = ZoneInfo("Asia/Tokyo")

MAILS = [
    {
        "id": "m1",
        "subject": "【演習】第3回レポート提出のお願い",
        "from": "prof@example.ac.jp",
        "date": "Fri, 11 Sep 2026 09:00:00 +0900",
        "body": "第3回レポートの提出期限は9月20日(日) 17:00までです。",
        "url": "https://mail.google.com/mail/u/0/#all/m1",
    },
    {
        "id": "m2",
        "subject": "サーバメンテナンスのお知らせ",
        "from": "sys@example.ac.jp",
        "received_at": "2026-09-11T09:00:00+09:00",
        "body": "9月15日に実施します。",
    },
    {
        "id": "m3",
        "subject": "奨学金申請書類について",
        "from": "shogaku@example.ac.jp",
        "received_at": "2026-09-11T09:00:00+09:00",
        "body": "申請書類の提出をお願いします。日程は追ってご連絡します。",
    },
]


def run_parse(tmp_path, mails=MAILS, extra=()):
    path = tmp_path / "mails.json"
    path.write_text(json.dumps(mails, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["parse", str(path), *extra]) == 0


@pytest.mark.parametrize(
    "value,expected_hour",
    [
        ("Fri, 11 Sep 2026 09:00:00 +0900", 9),
        ("2026-09-11T09:00:00+09:00", 9),
        ("2026-09-11T00:00:00Z", 9),  # UTC から JST へ
        ("2026-09-11 09:00:00", 9),  # タイムゾーンなしは設定のものとみなす
    ],
)
def test_parse_datetime_accepts_common_formats(value, expected_hour):
    assert parse_datetime(value, TOKYO).hour == expected_hour


def test_message_from_dict_accepts_alternative_key_names():
    message = message_from_dict(
        {"messageId": "x1", "subject": "件名", "from": "a@example.com",
         "date": "2026-09-11T09:00:00+09:00", "text": "本文"},
        TOKYO,
    )
    assert message.id == "x1" and message.sender == "a@example.com" and message.body == "本文"


def test_message_without_an_id_is_rejected():
    with pytest.raises(ValueError, match="id"):
        message_from_dict({"subject": "件名"}, TOKYO)


def test_parse_outputs_calendar_ready_events(tmp_path, capsys):
    run_parse(tmp_path)
    result = json.loads(capsys.readouterr().out)

    assert result["counts"] == {"total": 3, "submissions": 1, "needs_review": 1, "ignored": 1}
    event = result["submissions"][0]["event"]
    assert event["summary"] == "[提出] 【演習】第3回レポート提出のお願い"
    assert event["startTime"] == "2026-09-20T16:30:00+09:00"
    assert event["endTime"] == "2026-09-20T17:00:00+09:00"
    assert event["allDay"] is False
    assert event["overrideReminders"][0] == {"method": "popup", "minutes": 1440}


def test_event_description_carries_the_marker(tmp_path, capsys):
    run_parse(tmp_path)
    submission = json.loads(capsys.readouterr().out)["submissions"][0]
    assert submission["marker"] == "subcal:gmail:m1"
    assert submission["marker"] in submission["event"]["description"]


def test_submissions_without_a_deadline_go_to_needs_review(tmp_path, capsys):
    run_parse(tmp_path)
    review = json.loads(capsys.readouterr().out)["needs_review"]
    assert [item["subject"] for item in review] == ["奨学金申請書類について"]
    assert review[0]["event"] is None


def test_submissions_are_sorted_by_deadline(tmp_path, capsys):
    mails = [
        {**MAILS[0], "id": "late", "body": "提出期限は10月20日です。"},
        {**MAILS[0], "id": "early", "body": "提出期限は9月20日です。"},
    ]
    run_parse(tmp_path, mails)
    result = json.loads(capsys.readouterr().out)
    assert [item["due_date"] for item in result["submissions"]] == ["2026-09-20", "2026-10-20"]


def test_text_format_is_human_readable(tmp_path, capsys):
    run_parse(tmp_path, extra=["--format", "text"])
    output = capsys.readouterr().out
    assert "締切: 2026-09-20 17:00" in output
    assert "要確認" in output


def test_parse_accepts_an_object_with_a_messages_key(tmp_path, capsys):
    path = tmp_path / "wrapped.json"
    path.write_text(json.dumps({"messages": MAILS}, ensure_ascii=False), encoding="utf-8")
    assert cli.main(["parse", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["total"] == 3


def test_parse_reads_stdin(tmp_path, monkeypatch, capsys):
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(MAILS, ensure_ascii=False)))
    assert cli.main(["parse"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["submissions"] == 1


def test_broken_json_exits_with_an_error(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{これは", encoding="utf-8")
    assert cli.main(["parse", str(path)]) == 2
    assert "JSON" in capsys.readouterr().err


def test_all_day_events_when_configured(tmp_path, capsys):
    config_path = tmp_path / "allday.yaml"
    config_path.write_text("calendar:\n  all_day_when_time_unknown: true\n", encoding="utf-8")
    run_parse(tmp_path, [{**MAILS[0], "body": "提出期限は9月20日です。"}],
              extra=["-c", str(config_path)])
    event = json.loads(capsys.readouterr().out)["submissions"][0]["event"]
    assert event["allDay"] is True
    assert event["startTime"].startswith("2026-09-20")


def test_imap_messages_get_an_imap_source_id():
    from submission_calendar.models import Deadline, Message, Submission

    message = Message(id="x@y", subject="件名", sender="a@b", body="本文",
                      received_at=datetime(2026, 9, 11, 9, 0), source="imap")
    submission = Submission(message=message, title="件名",
                            deadline=Deadline(date=datetime(2026, 9, 20).date()))
    result = submission_to_dict(submission, Config())
    assert result["source_id"] == "imap:x@y"
    assert result["marker"] == "subcal:imap:x@y"
