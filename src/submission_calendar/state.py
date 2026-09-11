"""処理済みメッセージの記録。

カレンダー側にも識別子を埋め込んでいるので重複作成はそちらでも防げるが、
毎回すべてのメールを再解析すると無駄なので、処理済みの ID をローカルに残す。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["State"]


class State:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # 壊れていても処理は続けられる（カレンダー側で重複は防げる）
            return
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            self.entries = data["entries"]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "entries": self.entries}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, source_id: str) -> dict | None:
        return self.entries.get(source_id)

    def fingerprint_of(self, source_id: str) -> str | None:
        entry = self.entries.get(source_id)
        return entry.get("fingerprint") if entry else None

    def record(self, source_id: str, *, status: str, event_id: str = "",
               fingerprint: str = "", title: str = "") -> None:
        self.entries[source_id] = {
            "status": status,
            "event_id": event_id,
            "fingerprint": fingerprint,
            "title": title,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def forget(self, source_id: str) -> None:
        self.entries.pop(source_id, None)
