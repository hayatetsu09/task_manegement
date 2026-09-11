"""Claude を使う抽出器のテスト。API は呼ばず、偽クライアントで応答を差し替える。"""

import json
from datetime import date, datetime, time

import pytest

from submission_calendar.config import DetectionConfig, LLMConfig
from submission_calendar.extract.llm import AutoExtractor, LLMExtractor
from submission_calendar.extract.rules import RuleExtractor
from submission_calendar.models import Message

RECEIVED = datetime(2026, 9, 11, 9, 0)


def message(subject="課題提出のお願い", body="期日は追ってお知らせします。"):
    return Message(id="m1", subject=subject, sender="prof@example.ac.jp",
                   received_at=RECEIVED, body=body)


def answer(**overrides):
    payload = {
        "is_submission_request": True,
        "title": "第3回レポート",
        "due_date": "2026-09-20",
        "due_time": "17:00",
        "deadline_text": "9月20日 17時",
        "confidence": 0.9,
        "reason": "レポートの提出を求めている",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class FakeBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeBlock(text)]
        self.stop_reason = stop_reason


class FakeMessagesAPI:
    def __init__(self, response, fail_with=None):
        self.response = response
        self.fail_with = fail_with
        self.calls: list[dict] = []

    def create(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response, beta_fails_with=None):
        self.messages = FakeMessagesAPI(response)
        self.beta = type("Beta", (), {"messages": FakeMessagesAPI(response, beta_fails_with)})()


def build(response_text, stop_reason="end_turn", beta_fails_with=None):
    client = FakeClient(FakeResponse(response_text, stop_reason), beta_fails_with)
    return LLMExtractor(LLMConfig(), DetectionConfig(), client=client), client


def test_parses_the_response_into_a_submission():
    extractor, _ = build(answer())
    result = extractor.extract(message())
    assert result.title == "第3回レポート"
    assert result.deadline.date == date(2026, 9, 20)
    assert result.deadline.time == time(17, 0)
    assert result.extractor == "llm"


def test_request_uses_the_configured_model_and_schema():
    extractor, client = build(answer())
    extractor.extract(message())
    request = client.beta.messages.calls[0]
    assert request["model"] == "claude-opus-5"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "受信日時: 2026-09-11" in request["messages"][0]["content"]


def test_missing_due_time_gives_a_date_only_deadline():
    extractor, _ = build(answer(due_time=""))
    assert extractor.extract(message()).deadline.time is None


def test_missing_due_date_keeps_the_submission_without_a_deadline():
    extractor, _ = build(answer(due_date=""))
    result = extractor.extract(message())
    assert result is not None and result.deadline is None


def test_not_a_submission_returns_none():
    extractor, _ = build(answer(is_submission_request=False))
    assert extractor.extract(message()) is None


def test_refusal_is_handled():
    extractor, _ = build(answer(), stop_reason="refusal")
    assert extractor.extract(message()) is None


def test_unparsable_response_is_handled():
    extractor, _ = build("JSON ではありません")
    assert extractor.extract(message()) is None


def test_falls_back_when_the_sdk_does_not_accept_fallbacks():
    extractor, client = build(answer(), beta_fails_with=TypeError("unexpected keyword 'fallbacks'"))
    assert extractor.extract(message()).title == "第3回レポート"
    assert len(client.messages.calls) == 1


# --- auto モード ------------------------------------------------------
@pytest.fixture
def auto_pair():
    extractor, client = build(answer())
    return AutoExtractor(RuleExtractor(DetectionConfig()), extractor), client


def test_auto_does_not_call_claude_when_rules_succeed(auto_pair):
    auto, client = auto_pair
    result = auto.extract(message("レポート提出のお願い", "提出期限は9月20日(日) 17:00までです。"))
    assert result.extractor == "rules"
    assert client.beta.messages.calls == []


def test_auto_calls_claude_when_the_deadline_is_missing(auto_pair):
    auto, client = auto_pair
    result = auto.extract(message("レポート提出のお願い", "期日は次回の講義の3日前までです。"))
    assert result.extractor == "llm"
    assert len(client.beta.messages.calls) == 1


def test_auto_skips_clearly_unrelated_mail(auto_pair):
    auto, client = auto_pair
    assert auto.extract(message("メンテナンスのお知らせ", "9月15日に実施します。")) is None
    assert client.beta.messages.calls == []


def test_auto_keeps_the_rule_result_when_claude_fails():
    rules = RuleExtractor(DetectionConfig())
    broken = LLMExtractor(LLMConfig(), DetectionConfig(), client=FakeClient(None))
    broken._supports_fallbacks = False
    broken.client.messages.fail_with = RuntimeError("接続できません")
    result = AutoExtractor(rules, broken).extract(
        message("レポート提出のお願い", "期日は追ってお知らせします。")
    )
    assert result is not None and result.extractor == "rules"
