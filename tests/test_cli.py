"""CLI の end-to-end テスト（Google API は偽物に差し替える）。"""

from datetime import datetime

import pytest
from fakes import (FakeCalendarService, FakeGmailService, FakeGraphSource,
                   FakeImapSource, raw_message)

from submission_calendar import cli

RECEIVED = datetime(2026, 9, 11, 9, 0)

MAILS = [
    raw_message(
        "m1",
        "【情報処理演習】第3回レポート提出のお願い",
        "第3回レポートの提出期限は9月20日(日) 17:00までです。",
        received=RECEIVED,
    ),
    raw_message(
        "m2",
        "サーバメンテナンスのお知らせ",
        "9月15日にメンテナンスを行います。",
        received=RECEIVED,
    ),
    raw_message(
        "m3",
        "奨学金申請書類について",
        "申請書類の提出をお願いします。日程は追ってご連絡します。",
        received=RECEIVED,
    ),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    gmail = FakeGmailService(list(MAILS))
    calendar = FakeCalendarService()
    monkeypatch.setattr(cli, "_connect", lambda config, **kwargs: (gmail, calendar))

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"state_file: {tmp_path / 'state.json'}\n"
        f"token_file: {tmp_path / 'token.json'}\n",
        encoding="utf-8",
    )
    return {
        "gmail": gmail,
        "calendar": calendar,
        "args": ["-c", str(config_path)],
        "state": tmp_path / "state.json",
    }


def events(env):
    return env["calendar"].events()


def test_sync_creates_one_event_for_the_assignment(env, capsys):
    assert cli.main(["sync", *env["args"]]) == 0
    stored = list(events(env).store.values())
    assert len(stored) == 1
    assert stored[0]["summary"] == "[提出] 【情報処理演習】第3回レポート提出のお願い"
    assert stored[0]["end"]["dateTime"] == "2026-09-20T17:00:00"

    output = capsys.readouterr().out
    assert "+ 作成" in output
    assert "対象外 1" in output  # メンテナンスのお知らせ


def test_running_twice_does_not_duplicate(env, capsys):
    cli.main(["sync", *env["args"]])
    capsys.readouterr()
    assert cli.main(["sync", *env["args"]]) == 0
    assert events(env).inserts == 1
    assert "= 変更なし" in capsys.readouterr().out


def test_submission_without_a_deadline_is_reported_but_not_created(env, capsys):
    cli.main(["sync", *env["args"]])
    output = capsys.readouterr().out
    assert "- スキップ" in output
    assert "奨学金申請書類について" in output
    assert len(events(env).store) == 1


def test_dry_run_writes_nothing(env, capsys):
    assert cli.main(["sync", "--dry-run", *env["args"]]) == 0
    assert events(env).inserts == 0
    assert not env["state"].exists()
    assert "dry-run" in capsys.readouterr().out


def test_scan_does_not_touch_the_calendar(env, capsys):
    assert cli.main(["scan", *env["args"]]) == 0
    assert events(env).inserts == 0
    output = capsys.readouterr().out
    assert "2026-09-20 17:00" in output
    assert "要確認" in output


def test_state_lets_the_second_run_skip_the_calendar_lookup(env):
    cli.main(["sync", *env["args"]])
    before = events(env).store.copy()
    events(env).list = lambda **kwargs: (_ for _ in ()).throw(AssertionError("APIを呼ぶべきではない"))
    assert cli.main(["sync", *env["args"]]) == 0
    assert events(env).store == before


def test_all_flag_re_checks_processed_mail(env):
    cli.main(["sync", *env["args"]])
    assert cli.main(["sync", "--all", *env["args"]]) == 0
    assert events(env).inserts == 1  # 既存の予定を見つけるので作り直さない


def test_label_is_applied_to_processed_mail(env, tmp_path):
    config_path = tmp_path / "labelled.yaml"
    config_path.write_text(
        f"state_file: {tmp_path / 'state2.json'}\ngmail:\n  label_processed: 提出物/登録済み\n",
        encoding="utf-8",
    )
    assert cli.main(["sync", "-c", str(config_path)]) == 0
    modified = env["gmail"].messages().modified
    assert [message_id for message_id, _ in modified] == ["m1"]


def test_query_override_reaches_gmail(env):
    cli.main(["scan", "-q", "label:大学", "--since", "7", *env["args"]])
    assert env["gmail"].messages().last_query.startswith("newer_than:7d label:大学")


def test_failed_sync_exits_non_zero(env, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("API が壊れました")

    events(env).insert = boom
    assert cli.main(["sync", *env["args"]]) == 1


def test_init_config_writes_a_template(tmp_path, capsys):
    path = tmp_path / "new.yaml"
    assert cli.main(["init-config", str(path)]) == 0
    assert "timezone: Asia/Tokyo" in path.read_text(encoding="utf-8")
    assert cli.main(["init-config", str(path)]) == 1  # 上書きは拒否
    assert cli.main(["init-config", str(path), "--force"]) == 0


def test_config_error_exits_with_code_2(tmp_path, capsys):
    path = tmp_path / "bad.yaml"
    path.write_text("calender: {}\n", encoding="utf-8")
    assert cli.main(["sync", "-c", str(path)]) == 2
    assert "設定エラー" in capsys.readouterr().err


# --- IMAP（Outlook / 大学メール）経由 -----------------------------------
@pytest.fixture
def imap_env(env, monkeypatch, tmp_path):
    imap_source = FakeImapSource(
        [
            (
                "【教務課】履修登録確認票の提出について",
                "確認票は10月5日(月) 17:00までに教務課へ提出してください。",
            ),
            ("学内ネットワーク停止のお知らせ", "10月1日に停止します。"),
        ]
    )
    monkeypatch.setattr(cli, "_build_imap_source", lambda config: imap_source)
    config_path = tmp_path / "imap.yaml"
    config_path.write_text(
        f"state_file: {tmp_path / 'imap-state.json'}\n"
        "imap:\n  enabled: true\n  username: student@example.ac.jp\n",
        encoding="utf-8",
    )
    env["imap_args"] = ["-c", str(config_path)]
    env["imap_source"] = imap_source
    return env


def test_imap_mail_is_registered(imap_env, capsys):
    assert cli.main(["sync", *imap_env["imap_args"]]) == 0
    summaries = [event["summary"] for event in events(imap_env).store.values()]
    assert "[提出] 【教務課】履修登録確認票の提出について" in summaries


def test_imap_and_gmail_can_run_together(imap_env):
    assert cli.main(["sync", "--source", "all", *imap_env["imap_args"]]) == 0
    assert len(events(imap_env).store) == 2  # Gmail から 1 件、IMAP から 1 件


def test_imap_only_does_not_need_gmail_access(imap_env, monkeypatch):
    """IMAP だけの構成では Gmail のスコープを要求しない。"""
    captured = {}

    def fake_connect(config, *, need_calendar):
        captured["need_gmail"] = config.gmail.enabled
        return None, imap_env["calendar"]

    monkeypatch.setattr(cli, "_connect", fake_connect)
    assert cli.main(["sync", "--source", "imap", *imap_env["imap_args"]]) == 0
    assert captured["need_gmail"] is False


def test_imap_messages_are_not_labelled(imap_env):
    """ラベル付けは Gmail のメールにだけ行う。"""
    cli.main(["sync", "--source", "all", *imap_env["imap_args"]])
    labelled = [message_id for message_id, _ in imap_env["gmail"].messages().modified]
    assert all(not message_id.startswith("imap") for message_id in labelled)


# --- Outlook（Microsoft Graph）経由 --------------------------------------
@pytest.fixture
def outlook_env(env, monkeypatch, tmp_path):
    graph_source = FakeGraphSource(
        [
            ("【教務課】履修登録確認票の提出について",
             "確認票は10月5日(月) 17:00までに教務課へ提出してください。"),
            ("学内ネットワーク停止のお知らせ", "10月1日に停止します。"),
        ]
    )
    monkeypatch.setattr(cli, "_build_graph_source", lambda config: graph_source)
    config_path = tmp_path / "outlook.yaml"
    config_path.write_text(
        f"state_file: {tmp_path / 'outlook-state.json'}\ngraph:\n  enabled: true\n",
        encoding="utf-8",
    )
    env["outlook_args"] = ["-c", str(config_path)]
    env["graph_source"] = graph_source
    return env


def test_outlook_mail_is_registered(outlook_env):
    assert cli.main(["sync", *outlook_env["outlook_args"]]) == 0
    summaries = [event["summary"] for event in events(outlook_env).store.values()]
    assert "[提出] 【教務課】履修登録確認票の提出について" in summaries


def test_outlook_event_links_back_to_outlook(outlook_env):
    cli.main(["sync", "--source", "outlook", *outlook_env["outlook_args"]])
    event = next(iter(events(outlook_env).store.values()))
    assert "outlook.office365.com" in event["description"]


def test_source_outlook_skips_gmail(outlook_env, monkeypatch):
    captured = {}

    def fake_connect(config, *, need_calendar):
        captured["gmail"] = config.gmail.enabled
        captured["graph"] = config.graph.enabled
        return None, outlook_env["calendar"]

    monkeypatch.setattr(cli, "_connect", fake_connect)
    assert cli.main(["sync", "--source", "outlook", *outlook_env["outlook_args"]]) == 0
    assert captured == {"gmail": False, "graph": True}


def test_source_accepts_a_comma_separated_list(outlook_env):
    assert cli.main(["sync", "--source", "gmail,outlook", *outlook_env["outlook_args"]]) == 0
    assert len(events(outlook_env).store) == 2  # Gmail から 1 件、Outlook から 1 件


def test_unknown_source_is_rejected(env, capsys):
    assert cli.main(["sync", "--source", "yahoo", *env["args"]]) == 2
    assert "--source に使えない値" in capsys.readouterr().err


def test_source_all_keeps_the_config(outlook_env):
    """all は設定ファイルの指定をそのまま使う。"""
    assert cli.main(["sync", "--source", "all", *outlook_env["outlook_args"]]) == 0
    assert len(events(outlook_env).store) == 2
