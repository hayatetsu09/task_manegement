from pathlib import Path

import pytest
import yaml

from submission_calendar.config import EXAMPLE_CONFIG, Config, ConfigError

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_usable_without_a_file():
    config = Config()
    assert config.timezone == "Asia/Tokyo"
    assert config.calendar.calendar_id == "primary"
    assert config.extractor == "rules"


def test_partial_override_keeps_other_defaults():
    config = Config.from_dict({"calendar": {"event_prefix": "[課題] "}})
    assert config.calendar.event_prefix == "[課題] "
    assert config.calendar.default_due_time == "23:59"
    assert config.gmail.max_results == 50


def test_unknown_key_is_rejected():
    with pytest.raises(ConfigError, match="未知の項目"):
        Config.from_dict({"calender": {}})


def test_unknown_nested_key_is_rejected():
    with pytest.raises(ConfigError, match="calendar.colour"):
        Config.from_dict({"calendar": {"colour": "1"}})


def test_wrong_type_is_rejected():
    with pytest.raises(ConfigError, match="true / false"):
        Config.from_dict({"gmail": {"add_keyword_filter": "yes"}})


def test_bad_due_time_is_rejected():
    with pytest.raises(ConfigError, match="default_due_time"):
        Config.from_dict({"calendar": {"default_due_time": "夕方"}})


def test_unknown_extractor_is_rejected():
    with pytest.raises(ConfigError, match="extractor"):
        Config.from_dict({"extractor": "gpt"})


def test_too_many_reminders_are_rejected():
    with pytest.raises(ConfigError, match="5 件まで"):
        Config.from_dict({"calendar": {"reminders_minutes": [1, 2, 3, 4, 5, 6]}})


def test_load_from_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("timezone: Asia/Tokyo\ncalendar:\n  calendar_id: abc\n", encoding="utf-8")
    assert Config.load(path).calendar.calendar_id == "abc"


def test_load_missing_file_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="見つかりません"):
        Config.load(tmp_path / "none.yaml")


def test_paths_expand_the_home_directory():
    config = Config(token_file="~/token.json")
    assert "~" not in str(config.token_path)


def test_example_file_matches_the_template():
    """config.example.yaml と init-config の出力がずれないようにする。"""
    assert (REPO_ROOT / "config.example.yaml").read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_example_template_is_a_valid_config():
    Config.from_dict(yaml.safe_load(EXAMPLE_CONFIG))
