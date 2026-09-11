"""メール本文から日付・時刻を取り出すパーサ。

日本語のメールでよく使われる表記（「9月11日(金) 17時まで」「来週金曜」「今月末」など）と、
英語表記の両方を、正規表現だけで扱う。外部ライブラリには依存しない。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

__all__ = [
    "DateCandidate",
    "normalize",
    "strip_urls",
    "find_dates",
    "find_deadlines",
    "parse_time_string",
]

# 全角 → 半角。長音記号「ー」は単語の一部（レポート等）なので変換しない。
_TRANS = str.maketrans(
    {
        **{chr(0xFF10 + i): str(i) for i in range(10)},  # ０-９
        "：": ":",
        "／": "/",
        "．": ".",
        "，": ",",
        "～": "~",
        "〜": "~",
        "（": "(",
        "）": ")",
        "　": " ",
        "\xa0": " ",  # 非改行スペース（HTML メール由来）
        "－": "-",  # 全角ハイフンマイナス
        "−": "-",  # 数学記号のマイナス
    }
)

_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_WEEKDAY_CHARS = "月火水木金土日"

# 「9/11」等の年なし日付を解釈するとき、基準日より何日前までを「今年」と見なすか。
# これより古くなる場合は翌年の日付として扱う（12月のメールで「1/10締切」など）。
_PAST_TOLERANCE_DAYS = 45


@dataclass(frozen=True)
class DateCandidate:
    """本文中で見つかった日付（＋時刻）1 件。"""

    date: date
    time: time | None
    span: tuple[int, int]
    text: str
    explicit_year: bool = False

    def as_datetime(self, default: time) -> datetime:
        return datetime.combine(self.date, self.time or default)


def normalize(text: str) -> str:
    """全角数字・記号を半角に寄せる。"""
    return text.translate(_TRANS)


def strip_urls(text: str) -> str:
    """URL を空白に置き換える。

    URL に含まれる「/2026/09/11/」のような文字列を締め切りと誤認しないため。
    長さを変えないよう同じ文字数の空白に置換する（span がずれないように）。
    """
    return _URL_RE.sub(lambda m: " " * len(m.group(0)), text)


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(month: int, day: int, reference: date) -> date | None:
    """年が省略された日付に、基準日から見て自然な年を補う。"""
    for year in (reference.year, reference.year + 1, reference.year - 1):
        candidate = _make_date(year, month, day)
        if candidate is None:
            continue
        if (reference - candidate).days <= _PAST_TOLERANCE_DAYS:
            return candidate
    return None


def _end_of_month(anchor: date, month_offset: int = 0) -> date:
    month = anchor.month + month_offset
    year = anchor.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year, month, calendar.monthrange(year, month)[1])


def _weekday_date(reference: date, weekday: int, week_offset: int | None) -> date:
    """曜日表現を日付に変換する。

    week_offset が None（「金曜日まで」等）のときは基準日以降で最初のその曜日。
    0=今週 / 1=来週 / 2=再来週 のときは月曜始まりの週で数える。
    """
    if week_offset is None:
        return reference + timedelta(days=(weekday - reference.weekday()) % 7)
    monday = reference - timedelta(days=reference.weekday())
    return monday + timedelta(days=weekday + 7 * week_offset)


_ENGLISH_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_WEEK_OFFSETS = {"今週": 0, "来週": 1, "再来週": 2, "翌週": 1}


def _h_ymd(m: re.Match[str], _ref: date) -> date | None:
    return _make_date(int(m["y"]), int(m["m"]), int(m["d"]))


def _h_md(m: re.Match[str], ref: date) -> date | None:
    return _infer_year(int(m["m"]), int(m["d"]), ref)


def _h_english(m: re.Match[str], ref: date) -> date | None:
    month = _ENGLISH_MONTHS[m["mon"][:3].lower()]
    day = int(m["d"])
    if m["y"]:
        return _make_date(int(m["y"]), month, day)
    return _infer_year(month, day, ref)


def _h_relative_day(m: re.Match[str], ref: date) -> date | None:
    return ref + timedelta(days={"本日": 0, "今日": 0, "きょう": 0, "明日": 1, "あす": 1,
                                 "あした": 1, "明後日": 2, "あさって": 2,
                                 "today": 0, "tomorrow": 1}[m["word"].lower()])


def _h_month_end(m: re.Match[str], ref: date) -> date | None:
    if m["mon_num"]:
        anchor = _infer_year(int(m["mon_num"]), 1, ref)
        return _end_of_month(anchor) if anchor else None
    return _end_of_month(ref, 1 if m["rel"] == "来月" else 0)


def _h_weekday(m: re.Match[str], ref: date) -> date | None:
    weekday = _WEEKDAY_CHARS.index(m["wd"])
    return _weekday_date(ref, weekday, _WEEK_OFFSETS.get(m["week"] or ""))


# 並び順が優先順位。先に一致したものが span を占有し、後続ルールは重なる箇所を無視する。
# （「2026/9/11」が「9/11」として二重に拾われないようにするため）
_DATE_RULES: list[tuple[re.Pattern[str], object, bool]] = [
    # 2026年9月11日
    (re.compile(r"(?P<y>\d{4})\s*年\s*(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"), _h_ymd, True),
    # 2026/9/11, 2026-09-11, 2026.9.11
    (re.compile(r"(?<!\d)(?P<y>\d{4})\s*[/\-.]\s*(?P<m>\d{1,2})\s*[/\-.]\s*(?P<d>\d{1,2})(?!\d)"), _h_ymd, True),
    # 9月11日
    (re.compile(r"(?<!\d)(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"), _h_md, False),
    # 9/11（年なし）
    (re.compile(r"(?<![\d/\-.])(?P<m>\d{1,2})\s*/\s*(?P<d>\d{1,2})(?![\d/\-.])"), _h_md, False),
    # Sep 11, 2026 / September 11
    (re.compile(
        r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(?P<d>\d{1,2})"
        r"(?:st|nd|rd|th)?(?:\s*,?\s*(?P<y>\d{4}))?",
        re.IGNORECASE), _h_english, False),
    # 9月末 / 今月末 / 来月末 / 月末（「年末」「期末」などは対象外）
    (re.compile(r"(?:(?P<mon_num>\d{1,2})\s*月|(?P<rel>今月|来月)|(?<![年期学半先毎])月)\s*末日?"),
     _h_month_end, False),
    # 本日 / 明日 / 明後日 / today / tomorrow
    (re.compile(r"(?P<word>本日|今日|きょう|明日|あした|あす|明後日|あさって|\btoday\b|\btomorrow\b)",
                re.IGNORECASE), _h_relative_day, False),
    # 今週金曜日 / 来週月曜 / 金曜日
    (re.compile(r"(?P<week>今週|来週|再来週|翌週)?\s*(?P<wd>[月火水木金土日])\s*曜日?"), _h_weekday, False),
]

# 「9月11日(金)」の直後にある曜日かっこは日付の一部として飲み込む
_WEEKDAY_SUFFIX_RE = re.compile(r"\s*[(\[]\s*[月火水木金土日]\s*[)\]]")

_TIME_RULES: list[re.Pattern[str]] = [
    # 17:00 / 9:30:00
    re.compile(r"(?<!\d)(?P<h>\d{1,2})\s*:\s*(?P<mi>\d{2})(?::\d{2})?(?!\d)"),
    # 午後5時30分 / 17時 / 5時半
    re.compile(r"(?P<ampm>午前|午後)?\s*(?P<h>\d{1,2})\s*時\s*(?:(?P<mi>\d{1,2})\s*分|(?P<half>半))?"),
    # 5pm / 5:30 PM
    re.compile(r"(?<![\d:])(?P<h>\d{1,2})\s*(?P<ampm2>a\.?m\.?|p\.?m\.?)", re.IGNORECASE),
    # 正午
    re.compile(r"(?P<noon>正午)"),
]


def _time_from_match(m: re.Match[str]) -> time | None:
    groups = m.groupdict()
    if groups.get("noon"):
        return time(12, 0)

    hour = int(m["h"])
    minute = int(groups["mi"]) if groups.get("mi") else 0
    if groups.get("half"):
        minute = 30

    ampm = (groups.get("ampm") or groups.get("ampm2") or "").lower().replace(".", "")
    if ampm in ("午後", "pm") and hour < 12:
        hour += 12
    elif ampm in ("午前", "am") and hour == 12:
        hour = 0

    # 「24時まで」「24:00」はその日の終わりを指す
    if hour == 24 and minute == 0:
        return time(23, 59)
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_time_string(value: str) -> time:
    """設定ファイルの "23:59" のような文字列を time に変換する。"""
    hour, _, minute = normalize(value).strip().partition(":")
    return time(int(hour), int(minute or 0))


def _find_time_near(text: str, start: int, end: int, window: int = 28) -> tuple[time, int] | None:
    """日付の直後（既定で 28 文字以内）にある時刻を探す。

    戻り値は (時刻, 時刻表記の終了位置)。
    """
    tail = text[end : end + window]
    best: tuple[int, time, int] | None = None
    for pattern in _TIME_RULES:
        m = pattern.search(tail)
        if not m:
            continue
        parsed = _time_from_match(m)
        if parsed is None:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), parsed, end + m.end())
    if best is None:
        return None
    return best[1], best[2]


def _overlaps(span: tuple[int, int], used: list[tuple[int, int]]) -> bool:
    return any(span[0] < u_end and u_start < span[1] for u_start, u_end in used)


def find_dates(text: str, reference: date) -> list[DateCandidate]:
    """本文中の日付をすべて拾う（時刻は付けない）。

    reference は相対表現（明日・来週金曜など）の基準日で、通常はメールの受信日。
    """
    text = strip_urls(normalize(text))
    used: list[tuple[int, int]] = []
    found: list[DateCandidate] = []

    for pattern, handler, explicit_year in _DATE_RULES:
        for m in pattern.finditer(text):
            if not m.group(0).strip() or _overlaps(m.span(), used):
                continue
            try:
                resolved = handler(m, reference)  # type: ignore[operator]
            except (ValueError, KeyError):
                resolved = None
            if resolved is None:
                continue
            end = m.end()
            suffix = _WEEKDAY_SUFFIX_RE.match(text, end)
            if suffix:
                end = suffix.end()
            used.append((m.start(), end))
            found.append(
                DateCandidate(
                    date=resolved,
                    time=None,
                    span=(m.start(), end),
                    text=text[m.start() : end].strip(),
                    explicit_year=explicit_year,
                )
            )

    found.sort(key=lambda c: c.span[0])
    return found


def find_deadlines(text: str, reference: date) -> list[DateCandidate]:
    """日付を拾い、直後にある時刻を結びつけて返す。"""
    normalized = strip_urls(normalize(text))
    result: list[DateCandidate] = []
    for candidate in find_dates(text, reference):
        found = _find_time_near(normalized, *candidate.span)
        if found is None:
            result.append(candidate)
            continue
        parsed_time, time_end = found
        result.append(
            DateCandidate(
                date=candidate.date,
                time=parsed_time,
                span=(candidate.span[0], time_end),
                text=normalized[candidate.span[0] : time_end].strip(),
                explicit_year=candidate.explicit_year,
            )
        )
    return result
