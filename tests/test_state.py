from submission_calendar.state import State


def test_records_survive_a_reload(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    state.record("gmail:m1", status="created", event_id="evt1", fingerprint="abc", title="課題")
    state.save()

    reloaded = State(path)
    assert reloaded.get("gmail:m1")["event_id"] == "evt1"
    assert reloaded.fingerprint_of("gmail:m1") == "abc"


def test_missing_file_starts_empty(tmp_path):
    assert State(tmp_path / "none.json").entries == {}


def test_broken_file_does_not_crash(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{これはJSONではない", encoding="utf-8")
    assert State(path).entries == {}


def test_forget(tmp_path):
    state = State(tmp_path / "state.json")
    state.record("gmail:m1", status="created")
    state.forget("gmail:m1")
    assert state.get("gmail:m1") is None


def test_parent_directory_is_created(tmp_path):
    state = State(tmp_path / "nested" / "dir" / "state.json")
    state.record("gmail:m1", status="ignored")
    state.save()
    assert (tmp_path / "nested" / "dir" / "state.json").exists()
