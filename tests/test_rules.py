from datetime import date, datetime, time

import pytest

from submission_calendar.config import DetectionConfig
from submission_calendar.extract.rules import RuleExtractor, clean_title
from submission_calendar.models import Message

RECEIVED = datetime(2026, 9, 11, 9, 0)


def message(subject, body, sender="prof@example.ac.jp", received=RECEIVED):
    return Message(id="m1", subject=subject, sender=sender, received_at=received, body=body)


@pytest.fixture
def extractor():
    return RuleExtractor(DetectionConfig())


def test_typical_assignment_email(extractor):
    result = extractor.extract(
        message(
            "【情報処理演習】第3回レポート提出のお願い",
            "受講生各位\n\n第3回レポートの提出期限は9月20日(日) 17:00までです。\n"
            "LMS からPDFをアップロードしてください。",
        )
    )
    assert result is not None
    assert result.deadline.date == date(2026, 9, 20)
    assert result.deadline.time == time(17, 0)
    assert result.title == "【情報処理演習】第3回レポート提出のお願い"


def test_deadline_at_end_of_a_range_is_chosen(extractor):
    result = extractor.extract(
        message("健康診断の問診票", "問診票は10/1〜10/20までに提出してください。説明会は10月1日です。")
    )
    assert result.deadline.date == date(2026, 10, 20)


def test_relative_deadline(extractor):
    result = extractor.extract(message("ゼミの連絡", "来週金曜までにレジュメを提出してください。"))
    assert result.deadline.date == date(2026, 9, 18)


def test_deadline_only_in_subject(extractor):
    result = extractor.extract(message("【9/25締切】履修登録のお願い", "教務システムから手続きしてください。"))
    assert result.deadline.date == date(2026, 9, 25)


@pytest.mark.parametrize(
    "subject,body",
    [
        ("サーバメンテナンスのお知らせ", "9月15日にメンテナンスを行います。"),
        ("懇親会のご案内", "10月3日に開催します。ぜひご参加ください。"),
        ("【特価】秋の新商品", "セール価格は9月30日までです。"),
    ],
)
def test_unrelated_mail_is_skipped(extractor, subject, body):
    assert extractor.extract(message(subject, body)) is None


def test_excluded_by_subject_keyword(extractor):
    assert extractor.extract(message("メールマガジン9月号", "課題の提出期限は9/20です。")) is None


def test_exclusion_does_not_look_at_the_body_footer():
    """フッターの「配信停止」で正当な依頼メールを落とさない。"""
    extractor = RuleExtractor(DetectionConfig(exclude_keywords=["unsubscribe"]))
    result = extractor.extract(
        message("課題提出のお願い", "9/20までに提出してください。\n--\n配信停止(unsubscribe)はこちら")
    )
    assert result is not None


def test_excluded_sender():
    extractor = RuleExtractor(DetectionConfig(exclude_senders=["noreply@shop.example"]))
    assert extractor.extract(
        message("課題の提出期限について", "9/20までです。", sender="noreply@shop.example")
    ) is None


def test_past_dates_are_ignored(extractor):
    result = extractor.extract(message("レポート提出のお願い", "提出期限は8月20日でした。"))
    assert result is not None
    assert result.deadline is None
    assert "締め切りが分からない" in " ".join(result.notes)


def test_submission_without_any_date(extractor):
    result = extractor.extract(message("課題提出のお願い", "詳細は追ってお知らせします。"))
    assert result is not None and result.deadline is None


def test_deadline_expression_alone_is_not_enough(extractor):
    """日付＋締切表現だけで、提出を示す語がなければ拾わない。"""
    assert extractor.extract(message("駐車場の利用について", "工事は9/20までです。")) is None


def test_min_score_can_be_relaxed():
    loose = RuleExtractor(DetectionConfig(min_score=1.0))
    assert loose.extract(message("アンケート", "回答をお願いします。")) is not None


def test_extra_keywords_are_counted():
    extractor = RuleExtractor(DetectionConfig(extra_keywords=["実習日誌"]))
    result = extractor.extract(message("実習日誌について", "9/20までに実習日誌を出してください。"))
    assert result is not None


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("Re: 課題について", "課題について"),
        ("RE: Fwd: 課題について", "課題について"),
        ("返信: 課題について", "課題について"),
        ("", "(件名なし)"),
        ("あ" * 150, "あ" * 99 + "…"),
    ],
)
def test_clean_title(subject, expected):
    assert clean_title(subject) == expected


def test_low_confidence_deadline_is_flagged(extractor):
    result = extractor.extract(
        message("課題提出のお願い", "提出をお願いします。10月3日に授業があります。")
    )
    assert result is not None
    assert any("確認" in note for note in result.notes)


# --- 差出人の許可リスト -------------------------------------------------
def test_only_senders_keeps_matching_mail():
    extractor = RuleExtractor(DetectionConfig(only_senders=["ac.jp"]))
    result = extractor.extract(
        message("レポート提出のお願い", "提出期限は9/20です。", sender="prof@example.ac.jp")
    )
    assert result is not None


def test_only_senders_drops_everything_else():
    """「応募締切」のある宣伝メールを差出人で落とせる。"""
    extractor = RuleExtractor(DetectionConfig(only_senders=["ac.jp"]))
    assert extractor.extract(
        message("【8/30締切】試写会のご案内", "応募締切は8月30日までです。", sender="info@shop.example.com")
    ) is None


def test_only_senders_matches_a_bare_domain_or_address():
    extractor = RuleExtractor(DetectionConfig(only_senders=["kyomu@example.ac.jp"]))
    assert extractor.extract(
        message("課題提出", "9/20までに提出。", sender="教務課 <kyomu@example.ac.jp>")
    ) is not None
    assert extractor.extract(
        message("課題提出", "9/20までに提出。", sender="other@example.ac.jp")
    ) is None


def test_empty_only_senders_allows_everything():
    extractor = RuleExtractor(DetectionConfig(only_senders=[]))
    assert extractor.extract(
        message("レポート提出のお願い", "提出期限は9/20です。", sender="anyone@example.com")
    ) is not None


@pytest.mark.parametrize("subject", [
    "【無料ご招待】試写会のお知らせ",
    "【抽選で当たる】プレゼントキャンペーン",
    "今だけ特価クーポン配布中",
])
def test_promotional_subjects_are_excluded(extractor, subject):
    """応募締切のある宣伝メールは既定の除外語で落ちる。"""
    assert extractor.extract(message(subject, "応募締切は8月30日までです。")) is None
