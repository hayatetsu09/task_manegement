"""Google の OAuth 認証と API クライアントの組み立て。

初回だけブラウザで同意し、以降はトークンファイルを使い回す。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SCOPE_GMAIL_READONLY", "SCOPE_GMAIL_MODIFY", "SCOPE_CALENDAR",
           "scopes_for", "get_credentials", "build_services", "MissingDependency"]

SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_MODIFY = "https://www.googleapis.com/auth/gmail.modify"
SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"


class MissingDependency(RuntimeError):
    pass


def scopes_for(*, need_gmail: bool = True, need_calendar: bool = True,
               need_label: bool = False) -> list[str]:
    """必要最小限のスコープを返す。

    Gmail を使わない（IMAP だけの）構成ではメールへのアクセス権を要求しない。
    ラベルを付けるときだけ読み取りではなく gmail.modify が要る。
    """
    scopes = []
    if need_gmail:
        scopes.append(SCOPE_GMAIL_MODIFY if need_label else SCOPE_GMAIL_READONLY)
    if need_calendar:
        scopes.append(SCOPE_CALENDAR)
    return scopes


def _imports():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:  # pragma: no cover - 実行環境の問題
        raise MissingDependency(
            "Google のライブラリが見つかりません。`pip install -e .` で導入してください。"
        ) from exc
    return Request, Credentials, InstalledAppFlow, build


def get_credentials(
    client_secret_path: Path,
    token_path: Path,
    scopes: list[str],
    *,
    allow_interactive: bool = True,
):
    """保存済みトークンを読み、無ければ（または権限が足りなければ）認証し直す。"""
    Request, Credentials, InstalledAppFlow, _ = _imports()

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        except ValueError:
            creds = None

    if creds and not set(scopes).issubset(set(creds.scopes or [])):
        creds = None  # 必要な権限が増えたので取り直す

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not allow_interactive:
            raise RuntimeError("認証が必要です。`subcal auth` を実行してください。")
        if not client_secret_path.exists():
            raise FileNotFoundError(
                f"OAuth クライアント情報が見つかりません: {client_secret_path}\n"
                "Google Cloud コンソールで作成した認証情報 (JSON) を置いてください。"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
        creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    return creds


def build_services(creds, *, gmail: bool = True, calendar: bool = True) -> tuple:
    """(gmail, calendar) のサービスオブジェクトを作る。不要な方は None。"""
    _, _, _, build = _imports()
    return (
        build("gmail", "v1", credentials=creds, cache_discovery=False) if gmail else None,
        build("calendar", "v3", credentials=creds, cache_discovery=False) if calendar else None,
    )
