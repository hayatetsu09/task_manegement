"""Claude (Anthropic API) を使う抽出器（任意機能）。

正規表現では読み取れない書き方（「次回の講義の3日前まで」など）に対応したいときに使う。
`pip install -e ".[llm]"` と環境変数 ANTHROPIC_API_KEY が必要。
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, time

from ..config import DetectionConfig, LLMConfig
from ..models import Deadline, Message, Submission
from .rules import RuleExtractor, clean_title

__all__ = ["LLMExtractor", "AutoExtractor"]

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
あなたはメールを読んで「提出物の依頼」を見つける係です。

提出依頼とは、受信者が期日までに何かを提出・提供・回答・申請する必要があるものを指します
（課題やレポート、申請書類、アンケートの回答、申込みなど）。
単なる案内・広告・イベント告知・すでに提出済みの確認は提出依頼ではありません。

締め切りは、メールの受信日時を基準に実際の日付へ変換してください
（「来週金曜」「今月末」「3日以内」なども具体的な日付にする）。
締め切りが本文から特定できない場合は due_date を空文字にしてください。
推測で日付を作らないでください。"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "is_submission_request": {
            "type": "boolean",
            "description": "提出物の依頼であれば true",
        },
        "title": {
            "type": "string",
            "description": "何を提出するのかが分かる短い日本語のタイトル（40文字以内）",
        },
        "due_date": {
            "type": "string",
            "description": "締め切り日 YYYY-MM-DD。特定できなければ空文字",
        },
        "due_time": {
            "type": "string",
            "description": "締め切り時刻 HH:MM（24時間制）。書かれていなければ空文字",
        },
        "deadline_text": {
            "type": "string",
            "description": "締め切りの根拠になったメール中の表記をそのまま",
        },
        "confidence": {
            "type": "number",
            "description": "判断の確からしさ 0.0〜1.0",
        },
        "reason": {
            "type": "string",
            "description": "そう判断した理由を一文で",
        },
    },
    "required": [
        "is_submission_request", "title", "due_date", "due_time",
        "deadline_text", "confidence", "reason",
    ],
    "additionalProperties": False,
}

# 安全性の判定でモデルが応答を拒否した場合に、別モデルへ自動で切り替えてもらう
_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _parse_time(value: str) -> time | None:
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).time()
        except (ValueError, AttributeError):
            continue
    return None


class LLMExtractor:
    name = "llm"

    def __init__(self, config: LLMConfig | None = None,
                 detection: DetectionConfig | None = None, client=None):
        self.config = config or LLMConfig()
        self.detection = detection or DetectionConfig()
        self._client = client
        self._supports_fallbacks = True

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise RuntimeError(
                    'Claude を使う抽出器には anthropic が必要です: pip install -e ".[llm]"'
                ) from exc
            self._client = anthropic.Anthropic()
        return self._client

    def _build_prompt(self, message: Message) -> str:
        return (
            f"受信日時: {message.received_at:%Y-%m-%d(%a) %H:%M}\n"
            f"差出人: {message.sender}\n"
            f"件名: {message.subject}\n"
            f"---- 本文ここから ----\n{message.body}\n---- 本文ここまで ----"
        )

    def _call(self, prompt: str):
        kwargs = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.config.effort,
                "format": {"type": "json_schema", "schema": _SCHEMA},
            },
        }
        if self._supports_fallbacks:
            try:
                return self.client.beta.messages.create(
                    **kwargs, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            except TypeError:
                # 古い SDK では fallbacks を渡せないので、以降は通常の呼び出しにする
                self._supports_fallbacks = False
        return self.client.messages.create(**kwargs)

    def extract(self, message: Message) -> Submission | None:
        response = self._call(self._build_prompt(message))

        if getattr(response, "stop_reason", None) == "refusal":
            logger.warning("Claude が応答を拒否しました: %s", message.subject)
            return None

        text = next((block.text for block in response.content if block.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Claude の応答を解釈できませんでした: %s", message.subject)
            return None

        if not data.get("is_submission_request"):
            return None

        due_date = _parse_date(data.get("due_date", ""))
        deadline = None
        if due_date is not None:
            deadline = Deadline(
                date=due_date,
                time=_parse_time(data.get("due_time", "")),
                text=data.get("deadline_text", "") or "",
                confidence=float(data.get("confidence", 0.5) or 0.5),
            )

        notes = []
        if reason := data.get("reason"):
            notes.append(f"Claude の判断: {reason}")
        if deadline is None:
            notes.append("締め切りが分からないため予定を作成できません")

        return Submission(
            message=message,
            title=(data.get("title") or "").strip() or clean_title(message.subject),
            deadline=deadline,
            score=float(data.get("confidence", 0.5) or 0.5) * 10,
            extractor=self.name,
            notes=notes,
        )


class AutoExtractor:
    """まずルールで処理し、取りこぼしたものだけ Claude に回す。

    明らかに無関係なメールは Claude に送らないので、費用を抑えられる。
    """

    name = "auto"

    def __init__(self, rules: RuleExtractor, llm: LLMExtractor):
        self.rules = rules
        self.llm = llm

    def extract(self, message: Message) -> Submission | None:
        if self.rules.is_excluded(message):
            return None

        result = self.rules.extract(message)
        if result is not None and result.has_deadline:
            return result

        # ルールが締め切りを取れなかったものだけ Claude に渡す。
        # ただし提出依頼らしさが全く無いメールは対象外にする。
        score, _ = self.rules.score(message)
        if result is None and score < self.rules.detection.min_score / 2:
            return None

        try:
            llm_result = self.llm.extract(message)
        except Exception as exc:  # API 障害でも rules の結果は活かす
            logger.warning("Claude での抽出に失敗しました (%s): %s", message.subject, exc)
            return result
        return llm_result or result
