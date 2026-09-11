"""提出依頼メールの取り込み元。"""

from .gmail import GmailSource, build_query

__all__ = ["GmailSource", "build_query"]
