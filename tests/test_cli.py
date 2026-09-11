"""CLI の end-to-end テスト（Google API は偽物に差し替える）。"""

from datetime import datetime

import pytest
from fakes import FakeCalendarService, FakeGmailService, raw_message

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
    monkeypatch.setattr(cli, "_connect", lambda config: (gmail, calendar))

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
