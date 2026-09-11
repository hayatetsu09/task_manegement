"""抽出器の共通インタフェース。"""

from __future__ import annotations

from typing import Protocol

from ..models import Message, Submission

__all__ = ["Extractor"]


class Extractor(Protocol):
    """1 通のメールから提出依頼を取り出す。

    提出依頼ではないと判断した場合は None を返す。締め切りが読み取れなかった場合は
    deadline=None の Submission を返す（利用者に「要確認」として見せるため）。
    """

    name: str

    def extract(self, message: Message) -> Submission | None: ...
