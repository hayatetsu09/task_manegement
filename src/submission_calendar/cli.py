"""コマンドラインインタフェース。

    subcal auth          Google の認証を済ませる（初回だけ）
    subcal scan          メールを解析して結果を表示する（カレンダーは変更しない）
    subcal sync          提出依頼を Google カレンダーに反映する
    subcal init-config   設定ファイルのひな形を書き出す
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import time
from pathlib import Path

from . import __version__
from .calendar_sync import CalendarSync, SyncResult
from .config import Config, ConfigError, EXAMPLE_CONFIG
from .extract import build_extractor
from .google_auth import build_services, get_credentials, scopes_for
from .models import Submission
from .sources.gmail import GmailSource, build_query
from .state import State

_MIDNIGHT = time(0, 0)

MARKERS = {
    "created": "+ 作成  ",
    "updated": "~ 更新  ",
    "unchanged": "= 変更なし",
    "skipped": "- スキップ",
    "failed": "! 失敗  ",
}


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-c", "--config", help="設定ファイルのパス")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳しく表示する")


def _add_search_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-q", "--query", help="Gmail の検索クエリ（設定を上書き）")
    parser.add_argument("-n", "--limit", type=int, help="調べるメールの最大件数")
    parser.add_argument("--since", type=int, metavar="DAYS", help="直近 N 日のメールだけを対象にする")
    parser.add_argument(
        "--extractor", choices=("rules", "llm", "auto"), help="抽出方法（設定を上書き）"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subcal",
        description="メールに届いた提出依頼を Google カレンダーにまとめます。",
    )
    parser.add_argument("--version", action="version", version=f"submission-calendar {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    auth = subparsers.add_parser("auth", help="Google の認証を行う")
    _add_common_arguments(auth)

    scan = subparsers.add_parser("scan", help="メールを解析して結果を表示する")
    _add_common_arguments(scan)
    _add_search_arguments(scan)

    sync = subparsers.add_parser("sync", help="提出依頼をカレンダーに反映する")
    _add_common_arguments(sync)
    _add_search_arguments(sync)
    sync.add_argument("--dry-run", action="store_true", help="書き込まずに結果だけ表示する")
    sync.add_argument("--all", action="store_true", help="処理済みの記録を無視して作り直す")
    sync.add_argument("--calendar", help="書き込み先のカレンダー ID（設定を上書き）")
    sync.add_argument("--no-label", action="store_true", help="処理済みラベルを付けない")

    init = subparsers.add_parser("init-config", help="設定ファイルのひな形を書き出す")
    init.add_argument("path", nargs="?", default="config.yaml", help="書き出し先（既定: config.yaml）")
    init.add_argument("-f", "--force", action="store_true", help="既存ファイルを上書きする")

    return parser


def _load_config(args: argparse.Namespace) -> Config:
    config = Config.load(getattr(args, "config", None))
    if getattr(args, "query", None):
        config.gmail.query = args.query
    if getattr(args, "limit", None):
        config.gmail.max_results = args.limit
    if getattr(args, "since", None):
        config.gmail.query = f"newer_than:{args.since}d {config.gmail.query}".strip()
    if getattr(args, "extractor", None):
        config.extractor = args.extractor
    if getattr(args, "calendar", None):
        config.calendar.calendar_id = args.calendar
    if getattr(args, "no_label", False):
        config.gmail.label_processed = ""
    config.validate()
    return config


def _connect(config: Config):
    """認証して (gmail, calendar) のサービスを返す。"""
    creds = get_credentials(
        config.credentials_path,
        config.token_path,
        scopes_for(need_label=bool(config.gmail.label_processed)),
    )
    return build_services(creds)


def _fetch_messages(config: Config, gmail_service):
    source = GmailSource(gmail_service, config.timezone)
    query = build_query(config.gmail.query, config.gmail.add_keyword_filter)
    print(f"Gmail を検索中 (最大 {config.gmail.max_results} 件)")
    print(f"  クエリ: {query}")
    messages = source.search(query, config.gmail.max_results)
    print(f"  {len(messages)} 件のメールを取得しました\n")
    return source, messages


def _describe(submission: Submission) -> str:
    if submission.deadline is None:
        return "締切不明"
    deadline = submission.deadline
    text = deadline.describe()
    if deadline.text:
        text += f"  （メールの表記: {deadline.text}）"
    return text


def _print_submission(marker: str, submission: Submission, detail: str = "", verbose: bool = False) -> None:
    suffix = f"  — {detail}" if detail else ""
    print(f"{marker}  {submission.title}{suffix}")
    print(f"          締切: {_describe(submission)}")
    if verbose:
        print(f"          差出人: {submission.message.sender}")
        print(f"          判定: {submission.extractor} / スコア {submission.score:.1f}")
    for note in submission.notes:
        print(f"          ※ {note}")


# --- サブコマンド ------------------------------------------------------
def command_auth(args: argparse.Namespace) -> int:
    config = _load_config(args)
    creds = get_credentials(
        config.credentials_path,
        config.token_path,
        scopes_for(need_label=bool(config.gmail.label_processed)),
    )
    print(f"認証が完了しました。トークン: {config.token_path}")
    return 0 if creds else 1


def command_scan(args: argparse.Namespace) -> int:
    config = _load_config(args)
    gmail_service, _ = _connect(config)
    _, messages = _fetch_messages(config, gmail_service)

    extractor = build_extractor(config)
    submissions = [s for s in (extractor.extract(m) for m in messages) if s is not None]

    if not submissions:
        print("提出依頼らしいメールは見つかりませんでした。")
        return 0

    with_deadline = [s for s in submissions if s.has_deadline]
    with_deadline.sort(key=lambda s: (s.deadline.date, s.deadline.time or _MIDNIGHT))
    for submission in with_deadline:
        _print_submission("  提出", submission, verbose=args.verbose)

    unknown = [s for s in submissions if not s.has_deadline]
    for submission in unknown:
        _print_submission("  要確認", submission, verbose=args.verbose)

    print(f"\n{len(submissions)} 件が提出依頼と判定されました"
          f"（締切あり {len(with_deadline)} / 締切不明 {len(unknown)}）")
    print("カレンダーに登録するには `subcal sync` を実行してください。")
    return 0


def command_sync(args: argparse.Namespace) -> int:
    config = _load_config(args)
    gmail_service, calendar_service = _connect(config)
    source, messages = _fetch_messages(config, gmail_service)

    extractor = build_extractor(config)
    syncer = CalendarSync(calendar_service, config)
    state = State(config.state_path)
    label_id = ""
    if config.gmail.label_processed and not args.dry_run:
        label_id = source.ensure_label(config.gmail.label_processed)

    results: list[SyncResult] = []
    ignored = 0
    for message in messages:
        entry = None if args.all else state.get(message.source_id)
        if entry and entry.get("status") == "ignored" and config.extractor != "rules":
            ignored += 1  # 判定済みのメールを毎回 Claude に送らない
            continue

        submission = extractor.extract(message)
        if submission is None:
            ignored += 1
            state.record(message.source_id, status="ignored", title=message.subject)
            continue

        known = entry.get("fingerprint") if entry else None
        result = syncer.sync(submission, dry_run=args.dry_run, known_fingerprint=known)
        results.append(result)

        if result.ok and not args.dry_run:
            state.record(
                message.source_id,
                status=result.action,
                event_id=result.event_id or (entry or {}).get("event_id", ""),
                fingerprint=result.fingerprint,
                title=submission.title,
            )
            if label_id:
                try:
                    source.add_label(message.id, label_id)
                except Exception as exc:  # ラベル付けの失敗で処理を止めない
                    print(f"    ラベルを付けられませんでした: {exc}", file=sys.stderr)

        _print_submission(MARKERS[result.action], submission, result.detail, args.verbose)

    if not args.dry_run:
        state.save()

    counts = {action: 0 for action in MARKERS}
    for result in results:
        counts[result.action] += 1
    print("\n--- まとめ ---")
    print(
        f"作成 {counts['created']} / 更新 {counts['updated']} / 変更なし {counts['unchanged']} / "
        f"スキップ {counts['skipped']} / 失敗 {counts['failed']} / 対象外 {ignored}"
    )
    if args.dry_run:
        print("(dry-run のためカレンダーには書き込んでいません)")
    return 1 if counts["failed"] else 0


def command_init_config(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser()
    if path.exists() and not args.force:
        print(f"すでに存在します: {path}（上書きするには --force）", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"設定ファイルのひな形を書き出しました: {path}")
    return 0


_COMMANDS = {
    "auth": command_auth,
    "scan": command_scan,
    "sync": command_sync,
    "init-config": command_init_config,
}

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    try:
        return _COMMANDS[args.command](args)
    except ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n中断しました", file=sys.stderr)
        return 130
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
