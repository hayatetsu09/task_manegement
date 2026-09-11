"""キーワードと正規表現による抽出器。

外部サービスを使わないので無料・高速で、ふつうの提出依頼メールはこれで足りる。
判定は「提出依頼らしさのスコア付け」と「締め切り候補の選択」の 2 段階。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ..config import DetectionConfig
from ..dates import DateCandidate, find_deadlines, normalize, strip_urls
from ..models import Deadline, Message, Submission

__all__ = ["RuleExtractor", "clean_title", "STRONG_KEYWORDS", "WEAK_KEYWORDS"]

# 提出依頼を強く示す語（重み 2）
STRONG_KEYWORDS = (
    "提出期限", "提出締切", "提出締め切り", "提出日", "提出物", "提出先",
    "ご提出", "提出してください", "提出をお願い", "提出のお願い", "要提出",
    "締切", "締め切り", "〆切", "〆め切り", "期限", "必着", "厳守",
    "課題", "deadline", "due date", "submission", "submit",
)

# 補助的な語（重み 1）
WEAK_KEYWORDS = (
    "提出", "レポート", "宿題", "小テスト", "申込", "申し込み", "応募",
    "登録", "アップロード", "提出方法", "回答", "記入", "フォーム", "添付",
    "upload", "form", "assignment", "homework", "report",
)

# 締め切りらしさの手がかり。日付の近くにあると候補として優先する。
# 強い語（その日付が期日であることを示す）と弱い語（提出の話題であることを示す）を分ける。
_STRONG_CUES_JA = ("まで", "迄", "期限", "締切", "締め切り", "〆切", "必着", "厳守", "消印")
_WEAK_CUES_JA = ("提出", "出し", "回答", "返送", "送付", "申込", "登録")
_STRONG_CUES_EN = re.compile(r"\b(due|deadline|by|no later than)\b", re.IGNORECASE)
_WEAK_CUES_EN = re.compile(r"\b(submit|submission|send)\b", re.IGNORECASE)

_SUBJECT_PREFIX_RE = re.compile(r"^\s*(?:re|fwd?|返信|転送)\s*[:：]\s*", re.IGNORECASE)
_SPACE_RE = re.compile(r"\s+")

_MAX_TITLE_LENGTH = 100

# これ以上の確度で締め切りが取れたら「日付＋締切表現」として加点する
_CLEAR_DEADLINE_CONFIDENCE = 0.75


def clean_title(subject: str) -> str:
    """件名から Re:/Fwd: を落として予定のタイトルに使える形にする。"""
    title = subject or ""
    while True:
        stripped = _SUBJECT_PREFIX_RE.sub("", title)
        if stripped == title:
            break
        title = stripped
    title = _SPACE_RE.sub(" ", title).strip()
    if len(title) > _MAX_TITLE_LENGTH:
        title = title[: _MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return title or "(件名なし)"


def _has_strong_cue(text: str) -> bool:
    return any(word in text for word in _STRONG_CUES_JA) or bool(_STRONG_CUES_EN.search(text))


def _has_weak_cue(text: str) -> bool:
    return any(word in text for word in _WEAK_CUES_JA) or bool(_WEAK_CUES_EN.search(text))


class RuleExtractor:
    name = "rules"

    def __init__(self, detection: DetectionConfig | None = None):
        self.detection = detection or DetectionConfig()

    # --- 判定 ---------------------------------------------------------
    def is_excluded(self, message: Message) -> str | None:
        """除外理由を返す（除外しないなら None）。件名と差出人だけを見る。"""
        sender = message.sender.lower()
        if self.detection.only_senders:
            allowed = [name.lower() for name in self.detection.only_senders if name]
            if not any(name in sender for name in allowed):
                return "許可した差出人からのメールではありません"

        haystack = f"{message.subject}\n{message.sender}".lower()
        for word in self.detection.exclude_keywords:
            if word and word.lower() in haystack:
                return f"除外キーワード「{word}」"
        for excluded in self.detection.exclude_senders:
            if excluded and excluded.lower() in sender:
                return f"除外差出人「{excluded}」"
        return None

    def score(self, message: Message) -> tuple[float, list[str]]:
        """提出依頼らしさを採点する。件名に出てきた語は 2 倍で数える。"""
        subject = normalize(message.subject or "").lower()
        body = normalize(message.body or "").lower()
        total = 0.0
        hits: list[str] = []

        weighted = [(word, 2.0) for word in STRONG_KEYWORDS]
        weighted += [(word, 1.0) for word in WEAK_KEYWORDS]
        weighted += [(word, 1.0) for word in self.detection.extra_keywords if word]

        for word, weight in weighted:
            lowered = word.lower()
            if lowered in subject:
                total += weight * 2
                hits.append(f"件名:{word}")
            elif lowered in body:
                total += weight
                hits.append(f"本文:{word}")
        return total, hits

    # --- 締め切りの選択 -----------------------------------------------
    def _cue_score(self, text: str, span: tuple[int, int]) -> float:
        """日付の周辺にある手がかり語から、締め切りらしさを採点する。"""
        start, end = span
        before = text[max(0, start - 25) : start]
        after = text[end : end + 30]

        score = 0.0
        if _has_strong_cue(text[end : end + 6]):
            score += 3.0  # 「9/20まで」のように直後に続くものが最も確か
        elif _has_strong_cue(after):
            score += 1.0
        if _has_strong_cue(before):
            score += 2.0
        if _has_weak_cue(before) or _has_weak_cue(after):
            score += 1.0
        if any(token in text[max(0, start - 6) : start] for token in ("~", "から", "-")):
            score += 1.0  # 「10/1~10/20」のような期間は終わりの方が締め切り
        return score

    def pick_deadline(self, message: Message) -> tuple[Deadline | None, list[str]]:
        text = strip_urls(normalize(message.searchable_text))
        received = message.received_at.date()
        candidates = find_deadlines(message.searchable_text, received)
        if not candidates:
            return None, ["本文から日付を読み取れませんでした"]

        subject_end = len(normalize(message.subject or ""))
        earliest_allowed = received - timedelta(days=max(0, self.detection.ignore_past_days))

        scored: list[tuple[float, int, DateCandidate]] = []
        for candidate in candidates:
            if candidate.date < earliest_allowed:
                continue  # 受信より前の日付は締め切りではない（開催済みの案内など）
            weight = self._cue_score(text, candidate.span)
            if candidate.span[0] < subject_end:
                weight += 1.0  # 件名に書かれた日付は締め切りであることが多い
            scored.append((weight, -candidate.date.toordinal(), candidate))

        if not scored:
            return None, ["見つかった日付がすべて受信日より前でした"]

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        weight, _, best = scored[0]
        notes: list[str] = []
        if weight < 2.0:
            notes.append("締め切りを示す語が近くにないため、日付が正しいか確認してください")
        confidence = min(1.0, 0.4 + weight * 0.12 + (0.1 if best.time else 0.0))
        return (
            Deadline(date=best.date, time=best.time, text=best.text, confidence=confidence),
            notes,
        )

    # --- 本体 ---------------------------------------------------------
    def extract(self, message: Message) -> Submission | None:
        excluded = self.is_excluded(message)
        if excluded:
            return None

        total, hits = self.score(message)
        deadline, notes = self.pick_deadline(message)

        # 「9/20まで」「10月5日必着」のように日付と締め切り表現が直結している場合は、
        # それ自体が提出依頼の強い手がかりなので加点する。
        if deadline is not None and deadline.confidence >= _CLEAR_DEADLINE_CONFIDENCE:
            total += 2.0
            hits.append(f"締切表現:{deadline.text}")

        if total < self.detection.min_score:
            return None

        if deadline is None:
            notes = notes + ["締め切りが分からないため予定を作成できません"]

        return Submission(
            message=message,
            title=clean_title(message.subject),
            deadline=deadline,
            score=total,
            extractor=self.name,
            notes=notes,
        )
