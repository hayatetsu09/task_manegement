from datetime import date, datetime, time

import pytest
from fakes import FakeCalendarService

from submission_calendar.calendar_sync import EXT_FP_KEY, EXT_SOURCE_KEY, CalendarSync
from submission_calendar.config import Config
from submission_calendar.models import Deadline, Message, Submission


def make_submission(deadline_time=time(17, 0), title="レポート提出", deadline_date=date(2026, 9, 20)):
    message = Message(
        id="m1",
        subject="レポート提出のお願い",
        sender="prof@example.ac.jp",
        received_at=datetime(2026, 9, 11, 9, 0),
        body="本文",
        url="https://mail.google.com/mail/u/0/#all/m1",
    )
    deadline = None
    if deadline_date is not None:
        deadline = Deadline(date=deadline_date, time=deadline_time, text="9月20日 17:00")
    return Submission(message=message, title=title, deadline=deadline, score=10.0)


@pytest.fixture
def syncer():
    return CalendarSync(FakeCalendarService(), Config())


def test_creates_a_timed_event_ending_at_the_deadline(syncer):
    event = syncer.build_event(make_submission())
    assert event["start"]["dateTime"] == "2026-09-20T16:30:00"
    assert event["end"]["dateTime"] == "2026-09-20T17:00:00"
    assert event["start"]["timeZone"] == "Asia/Tokyo"
    assert event["summary"] == "[提出] レポート提出"


def test_default_time_is_used_when_the_mail_has_none(syncer):
    event = syncer.build_event(make_submission(deadline_time=None))
    assert event["end"]["dateTime"] == "2026-09-20T23:59:00"


def test_all_day_event_when_configured():
    config = Config()
    config.calendar.all_day_when_time_unknown = True
    syncer = CalendarSync(FakeCalendarService(), config)
    event = syncer.build_event(make_submission(deadline_time=None))
    assert event["start"] == {"date": "2026-09-20"}
    assert event["end"] == {"date": "2026-09-21"}  # 終日予定の終わりは翌日


def test_reminders_and_source_link(syncer):
    event = syncer.build_event(make_submission())
    assert event["reminders"]["overrides"] == [
        {"method": "popup", "minutes": 1440},
        {"method": "popup", "minutes": 180},
    ]
    assert event["source"]["url"].endswith("#all/m1")


def test_description_contains_the_mail_details(syncer):
    description = syncer.build_event(make_submission())["description"]
    assert "prof@example.ac.jp" in description
    assert "9月20日 17:00" in description


def test_event_is_tagged_with_the_source_id(syncer):
    private = syncer.build_event(make_submission())["extendedProperties"]["private"]
    assert private[EXT_SOURCE_KEY] == "gmail:m1"
    assert private[EXT_FP_KEY]


def test_sync_creates_then_stays_unchanged(syncer):
    first = syncer.sync(make_submission())
    assert first.action == "created"
    second = syncer.sync(make_submission())
    assert second.action == "unchanged"
    assert syncer.service.events().inserts == 1


def test_sync_updates_when_the_deadline_changes(syncer):
    syncer.sync(make_submission())
    result = syncer.sync(make_submission(deadline_time=time(23, 59)))
    assert result.action == "updated"
    assert syncer.service.events().inserts == 1
    assert syncer.service.events().patches == 1


def test_known_fingerprint_skips_api_calls(syncer):
    submission = make_submission()
    fingerprint = syncer.fingerprint(submission)
    result = syncer.sync(submission, known_fingerprint=fingerprint)
    assert result.action == "unchanged"
    assert syncer.service.events().inserts == 0


def test_dry_run_does_not_write(syncer):
    result = syncer.sync(make_submission(), dry_run=True)
    assert result.action == "created"
    assert syncer.service.events().inserts == 0


def test_submission_without_deadline_is_skipped(syncer):
    result = syncer.sync(make_submission(deadline_date=None))
    assert result.action == "skipped"
    assert "締め切り" in result.detail


def test_api_error_becomes_a_failed_result(syncer):
    def boom(**kwargs):
        raise RuntimeError("quota exceeded")

    syncer.service.events().list = boom
    result = syncer.sync(make_submission())
    assert result.action == "failed"
    assert "quota exceeded" in result.detail


def test_fingerprint_changes_with_the_title(syncer):
    assert syncer.fingerprint(make_submission()) != syncer.fingerprint(make_submission(title="別の課題"))
