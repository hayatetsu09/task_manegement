"""Microsoft Graph 経由の取り込みのテスト。通信は偽の関数に差し替える。"""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from submission_calendar.sources.graph import (
    DEFAULT_CLIENT_ID,
    GraphAuth,
    GraphError,
    GraphSource,
    message_from_graph,
)

TOKYO = ZoneInfo("Asia/Tokyo")


def graph_item(message_id="<abc@mail.kyutech.jp>", subject="【教務課】履修登録確認票の提出について",
               body="<p>確認票は10月5日(月) 17:00までに提出してください。</p>",
               content_type="html", received="2026-09-11T00:00:00Z"):
    return {
        "id": "AAMkAGQ1",
        "internetMessageId": message_id,
        "conversationId": "conv1",
        "subject": subject,
        "from": {"emailAddress": {"name": "教務課", "address": "kyomu@mail.kyutech.jp"}},
        "receivedDateTime": received,
        "webLink": "https://outlook.office365.com/mail/inbox/id/AAMkAGQ1",
        "body": {"contentType": content_type, "content": body},
    }


# --- メールの変換 -------------------------------------------------------
def test_html_body_is_converted_to_text():
    message = message_from_graph(graph_item(), TOKYO)
    assert message.body == "確認票は10月5日(月) 17:00までに提出してください。"


def test_plain_body_is_kept_as_is():
    message = message_from_graph(graph_item(body="提出期限は9/20です", content_type="text"), TOKYO)
    assert message.body == "提出期限は9/20です"


def test_received_time_is_converted_to_local_time():
    message = message_from_graph(graph_item(), TOKYO)
    assert message.received_at.hour == 9  # UTC 0時 = 日本時間 9時
    assert message.received_at.date() == date(2026, 9, 11)


def test_internet_message_id_is_used_as_the_id():
    message = message_from_graph(graph_item(), TOKYO)
    assert message.id == "abc@mail.kyutech.jp"
    assert message.source_id == "outlook:abc@mail.kyutech.jp"


def test_graph_id_is_used_when_there_is_no_internet_message_id():
    message = message_from_graph(graph_item(message_id=""), TOKYO)
    assert message.id == "AAMkAGQ1"


def test_web_link_becomes_the_event_link():
    assert message_from_graph(graph_item(), TOKYO).url.startswith("https://outlook.office365.com/")


def test_sender_includes_the_display_name():
    assert message_from_graph(graph_item(), TOKYO).sender == "教務課 <kyomu@mail.kyutech.jp>"


# --- デバイスコード認証 --------------------------------------------------
class FakePoster:
    """token / devicecode エンドポイントの応答を並べて返す。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, data):
        self.calls.append((url, data))
        return self.responses.pop(0)


DEVICE_CODE = {
    "user_code": "ABCD-EFGH",
    "device_code": "dev-code",
    "verification_uri": "https://microsoft.com/devicelogin",
    "interval": 1,
    "expires_in": 900,
}


def auth_with(responses, tmp_path):
    poster = FakePoster(responses)
    auth = GraphAuth(tmp_path / "token.json", post=poster, sleep=lambda _: None)
    return auth, poster


def test_login_saves_the_refresh_token(tmp_path):
    auth, poster = auth_with(
        [DEVICE_CODE, {"access_token": "at-1", "refresh_token": "rt-1"}], tmp_path
    )
    prompts = []
    assert auth.login(on_prompt=prompts.append) == "at-1"

    saved = json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "rt-1"
    assert saved["client_id"] == DEFAULT_CLIENT_ID
    assert "ABCD-EFGH" in prompts[0] and "devicelogin" in prompts[0]


def test_login_waits_while_the_user_signs_in(tmp_path):
    auth, poster = auth_with(
        [DEVICE_CODE,
         {"error": "authorization_pending"},
         {"error": "authorization_pending"},
         {"access_token": "at-1", "refresh_token": "rt-1"}],
        tmp_path,
    )
    assert auth.login(on_prompt=lambda _: None) == "at-1"
    assert len(poster.calls) == 4


def test_slow_down_is_respected(tmp_path):
    auth, _ = auth_with(
        [DEVICE_CODE, {"error": "slow_down"}, {"access_token": "at", "refresh_token": "rt"}],
        tmp_path,
    )
    assert auth.login(on_prompt=lambda _: None) == "at"


def test_rejected_sign_in_explains_the_tenant_restriction(tmp_path):
    auth, _ = auth_with(
        [DEVICE_CODE, {"error": "unauthorized_client", "error_description": "AADSTS700016"}],
        tmp_path,
    )
    with pytest.raises(GraphError, match="テナント"):
        auth.login(on_prompt=lambda _: None)


def test_saved_token_is_refreshed_without_asking_again(tmp_path):
    (tmp_path / "token.json").write_text(
        json.dumps({"refresh_token": "rt-old", "client_id": DEFAULT_CLIENT_ID,
                    "tenant": "organizations"}),
        encoding="utf-8",
    )
    auth, poster = auth_with([{"access_token": "at-2", "refresh_token": "rt-new"}], tmp_path)
    assert auth.access_token() == "at-2"
    assert poster.calls[0][1]["grant_type"] == "refresh_token"
    saved = json.loads((tmp_path / "token.json").read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "rt-new"


def test_missing_token_without_interaction_is_an_error(tmp_path):
    auth, _ = auth_with([], tmp_path)
    with pytest.raises(GraphError, match="auth-outlook"):
        auth.access_token(allow_interactive=False)


def test_has_token(tmp_path):
    auth, _ = auth_with([], tmp_path)
    assert auth.has_token is False
    (tmp_path / "token.json").write_text('{"refresh_token": "rt"}', encoding="utf-8")
    assert auth.has_token is True


# --- メールの取得 -------------------------------------------------------
class FakeAuth:
    def access_token(self, allow_interactive=True):
        return "token"


class FakeFetch:
    def __init__(self, pages):
        self.pages = list(pages)
        self.urls = []

    def __call__(self, url, token):
        self.urls.append(url)
        return self.pages.pop(0)


def test_search_returns_messages():
    fetch = FakeFetch([{"value": [graph_item(), graph_item(message_id="<x2@y>", subject="件名2")]}])
    messages = GraphSource(FakeAuth(), fetch=fetch).search(days=30, max_results=10)
    assert [m.subject for m in messages] == ["【教務課】履修登録確認票の提出について", "件名2"]


def test_search_filters_by_date_and_sorts_newest_first():
    fetch = FakeFetch([{"value": []}])
    GraphSource(FakeAuth(), fetch=fetch).search(days=30)
    url = fetch.urls[0]
    assert "receivedDateTime+ge+" in url
    assert "receivedDateTime+desc" in url
    assert "mailFolders/inbox/messages" in url


def test_search_follows_pagination():
    fetch = FakeFetch([
        {"value": [graph_item(message_id="<a@y>")], "@odata.nextLink": "https://next"},
        {"value": [graph_item(message_id="<b@y>")]},
    ])
    assert len(GraphSource(FakeAuth(), fetch=fetch).search(max_results=5)) == 2
    assert fetch.urls[1] == "https://next"


def test_search_stops_at_the_limit():
    fetch = FakeFetch([{"value": [graph_item(message_id=f"<{i}@y>") for i in range(10)]}])
    assert len(GraphSource(FakeAuth(), fetch=fetch).search(max_results=3)) == 3


def test_folder_can_be_changed():
    fetch = FakeFetch([{"value": []}])
    GraphSource(FakeAuth(), folder="archive", fetch=fetch).search()
    assert "mailFolders/archive/messages" in fetch.urls[0]
