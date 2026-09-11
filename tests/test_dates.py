from datetime import date, time

import pytest

from submission_calendar.dates import find_dates, find_deadlines, normalize, parse_time_string

REF = date(2026, 9, 11)  # 金曜日


def only_date(text, reference=REF):
    found = find_dates(text, reference)
    return found[0].date if found else None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2026年9月11日", date(2026, 9, 11)),
        ("2026/9/11", date(2026, 9, 11)),
        ("2026-09-11", date(2026, 9, 11)),
        ("2026.9.11", date(2026, 9, 11)),
        ("9月20日", date(2026, 9, 20)),
        ("9/20", date(2026, 9, 20)),
        ("９月２０日", date(2026, 9, 20)),  # 全角
        ("Oct 3, 2026", date(2026, 10, 3)),
        ("October 3", date(2026, 10, 3)),
        ("本日", date(2026, 9, 11)),
        ("明日", date(2026, 9, 12)),
        ("明後日", date(2026, 9, 13)),
        ("来週月曜", date(2026, 9, 14)),
        ("今週金曜日", date(2026, 9, 11)),
        ("再来週水曜", date(2026, 9, 23)),
        ("月曜日", date(2026, 9, 14)),  # 基準日以降で最初の月曜
        ("今月末", date(2026, 9, 30)),
        ("来月末", date(2026, 10, 31)),
        ("10月末", date(2026, 10, 31)),
        ("月末", date(2026, 9, 30)),
    ],
)
def test_dates_are_parsed(text, expected):
    assert only_date(text) == expected


def test_year_is_inferred_forward_across_new_year():
    # 12 月に届いた「1/10締切」は翌年
    assert only_date("1/10", date(2026, 12, 20)) == date(2027, 1, 10)


def test_recent_past_date_keeps_current_year():
    assert only_date("9/1", REF) == date(2026, 9, 1)


@pytest.mark.parametrize("text", ["年末調整", "期末試験", "第2四半期", "2月30日", "13月5日"])
def test_no_false_positives(text):
    assert find_dates(text, REF) == []


def test_urls_are_ignored():
    assert find_dates("https://example.com/2025/01/31/index.html", REF) == []


def test_full_date_is_not_also_read_as_month_day():
    found = find_dates("2026/9/11 に提出", REF)
    assert [c.date for c in found] == [date(2026, 9, 11)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("9月20日 17:00", time(17, 0)),
        ("9月20日(日) 17時", time(17, 0)),
        ("9月20日 午後5時30分", time(17, 30)),
        ("9月20日 午前9時", time(9, 0)),
        ("9月20日 5時半", time(5, 30)),
        ("9月20日 5pm", time(17, 0)),
        ("9月20日の正午", time(12, 0)),
        ("9月20日 24時", time(23, 59)),
        ("9月20日 23:59", time(23, 59)),
    ],
)
def test_times_attach_to_dates(text, expected):
    found = find_deadlines(text, REF)
    assert found[0].time == expected


def test_time_far_from_date_is_not_attached():
    text = "9月20日が締切です。" + "あ" * 40 + "17:00から説明会"
    assert find_deadlines(text, REF)[0].time is None


def test_weekday_suffix_is_part_of_the_date_text():
    found = find_deadlines("9月20日(日)まで", REF)
    assert found[0].text == "9月20日(日)"


def test_normalize_keeps_long_vowel_mark():
    assert normalize("レポート１") == "レポート1"


def test_parse_time_string():
    assert parse_time_string("23:59") == time(23, 59)
    assert parse_time_string("9") == time(9, 0)
