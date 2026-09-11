"""Microsoft Graph 経由でメールを取り込む（Outlook / Microsoft 365）。

Microsoft は 2022 年 10 月に Exchange Online の POP / IMAP 向けパスワード認証
（基本認証）を廃止したため、大学の Microsoft 365 メールはパスワードでは読めない。
ここではデバイスコードフローで OAuth の認証を行う。ブラウザで一度ログインすれば
更新トークンが保存され、以降は自動で読み取れる。

追加のライブラリは使わない（標準ライブラリの urllib のみ）。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import Message
from .gmail import html_to_text

__all__ = ["GraphAuth", "GraphSource", "GraphError", "DEFAULT_CLIENT_ID", "message_from_graph"]

# Microsoft が公開しているパブリッククライアント（Microsoft Graph Command Line Tools）。
# 多くのテナントで既に許可されているため、自分で Azure にアプリ登録しなくても使える。
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"

AUTHORITY = "https://login.microsoftonline.com"
GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
SCOPES = "https://graph.microsoft.com/Mail.Read offline_access"

_ANGLE_BRACKETS = re.compile(r"^<|>$")


class GraphError(RuntimeError):
    """Microsoft 側から返ってきたエラー。"""


def _post_form(url: str, data: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        try:
            return json.loads(body)  # OAuth のエラーは JSON で返る
        except json.JSONDecodeError:
            raise GraphError(f"{exc.code} {body}") from exc


def _get_json(url: str, access_token: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {access_token}"}, method="GET"
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise GraphError(f"Graph API の呼び出しに失敗しました ({exc.code}): {body}") from exc


class GraphAuth:
    """デバイスコードフローでアクセストークンを用意する。"""

    def __init__(
        self,
        token_path: Path | str,
        client_id: str = DEFAULT_CLIENT_ID,
        tenant: str = "organizations",
        *,
        post=_post_form,
        sleep=time.sleep,
    ):
        self.token_path = Path(token_path).expanduser()
        self.client_id = client_id
        self.tenant = tenant
        self._post = post
        self._sleep = sleep

    # --- トークンの保存 -------------------------------------------------
    def _load(self) -> dict:
        if not self.token_path.exists():
            return {}
        try:
            return json.loads(self.token_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, refresh_token: str) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(
            json.dumps(
                {"refresh_token": refresh_token, "client_id": self.client_id,
                 "tenant": self.tenant},
                indent=2,
            ),
            encoding="utf-8",
        )
        self.token_path.chmod(0o600)

    @property
    def has_token(self) -> bool:
        return bool(self._load().get("refresh_token"))

    # --- 認証 -----------------------------------------------------------
    def login(self, on_prompt=print) -> str:
        """ブラウザでの認証を促し、アクセストークンを返す。"""
        started = self._post(
            f"{AUTHORITY}/{self.tenant}/oauth2/v2.0/devicecode",
            {"client_id": self.client_id, "scope": SCOPES},
        )
        if "user_code" not in started:
            raise GraphError(_describe(started, "認証を開始できませんでした"))

        on_prompt(
            f"\nブラウザで {started['verification_uri']} を開き、"
            f"次のコードを入力してください:\n\n    {started['user_code']}\n\n"
            "大学のアカウントでサインインしたら、この画面に戻ってお待ちください。"
        )

        interval = int(started.get("interval", 5))
        deadline = time.monotonic() + int(started.get("expires_in", 900))
        while time.monotonic() < deadline:
            self._sleep(interval)
            result = self._post(
                f"{AUTHORITY}/{self.tenant}/oauth2/v2.0/token",
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": self.client_id,
                    "device_code": started["device_code"],
                },
            )
            error = result.get("error")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error:
                raise GraphError(_describe(result, "サインインが完了しませんでした"))
            self._save(result["refresh_token"])
            return result["access_token"]

        raise GraphError("サインインが時間内に完了しませんでした。もう一度実行してください。")

    def access_token(self, *, allow_interactive: bool = True) -> str:
        """保存済みの更新トークンからアクセストークンを取り直す。"""
        saved = self._load()
        refresh_token = saved.get("refresh_token")
        if not refresh_token:
            if not allow_interactive:
                raise GraphError("認証が必要です。`subcal auth-outlook` を実行してください。")
            return self.login()

        result = self._post(
            f"{AUTHORITY}/{saved.get('tenant', self.tenant)}/oauth2/v2.0/token",
            {
                "grant_type": "refresh_token",
                "client_id": saved.get("client_id", self.client_id),
                "refresh_token": refresh_token,
                "scope": SCOPES,
            },
        )
        if "access_token" not in result:
            if not allow_interactive:
                raise GraphError(_describe(result, "保存された認証が使えません"))
            return self.login()  # 期限切れなどの場合は取り直す
        if result.get("refresh_token"):
            self._save(result["refresh_token"])
        return result["access_token"]


def _describe(payload: dict, prefix: str) -> str:
    detail = payload.get("error_description") or payload.get("error") or payload
    text = f"{prefix}: {detail}"
    if payload.get("error") in ("invalid_client", "unauthorized_client", "invalid_grant"):
        text += (
            "\n大学のテナントがこのアプリを許可していない可能性があります。"
            "README の「大学メール（Outlook）を取り込む」を確認してください。"
        )
    return text


def message_from_graph(item: dict, tz: ZoneInfo) -> Message:
    body = item.get("body") or {}
    content = body.get("content") or item.get("bodyPreview") or ""
    if (body.get("contentType") or "").lower() == "html":
        content = html_to_text(content)

    sender = (item.get("from") or {}).get("emailAddress") or {}
    address = sender.get("address", "")
    name = sender.get("name", "")

    received = item.get("receivedDateTime") or ""
    try:
        parsed = datetime.fromisoformat(received.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.now(timezone.utc)

    identifier = _ANGLE_BRACKETS.sub("", (item.get("internetMessageId") or "").strip())
    return Message(
        id=identifier or item.get("id", ""),
        subject=item.get("subject") or "",
        sender=f"{name} <{address}>".strip() if name else address,
        received_at=parsed.astimezone(tz),
        body=content,
        source="outlook",
        url=item.get("webLink") or "",
        thread_id=item.get("conversationId", ""),
    )


class GraphSource:
    name = "outlook"

    _SELECT = ("id,subject,from,receivedDateTime,webLink,body,bodyPreview,"
               "internetMessageId,conversationId")

    def __init__(self, auth: GraphAuth, timezone_name: str = "Asia/Tokyo",
                 folder: str = "inbox", *, fetch=_get_json):
        self.auth = auth
        self.tz = ZoneInfo(timezone_name)
        self.folder = folder
        self._fetch = fetch

    def search(self, days: int = 60, max_results: int = 50) -> list[Message]:
        """直近 days 日のメールを新しい順に最大 max_results 件取得する。"""
        token = self.auth.access_token()
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urllib.parse.urlencode(
            {
                "$select": self._SELECT,
                "$filter": f"receivedDateTime ge {since}",
                "$orderby": "receivedDateTime desc",
                "$top": min(max_results, 100),
            }
        )
        url = f"{GRAPH_ROOT}/me/mailFolders/{self.folder}/messages?{query}"

        messages: list[Message] = []
        while url and len(messages) < max_results:
            payload = self._fetch(url, token)
            for item in payload.get("value", []):
                messages.append(message_from_graph(item, self.tz))
                if len(messages) >= max_results:
                    break
            url = payload.get("@odata.nextLink", "")
        return messages
