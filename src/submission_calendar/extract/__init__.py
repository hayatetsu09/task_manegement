"""メールから提出依頼を取り出す抽出器。"""

from .base import Extractor
from .rules import RuleExtractor, clean_title

__all__ = ["Extractor", "RuleExtractor", "clean_title", "build_extractor"]


def build_extractor(config):
    """設定の extractor 名から抽出器を組み立てる。"""
    rules = RuleExtractor(config.detection)
    if config.extractor == "rules":
        return rules
    from .llm import AutoExtractor, LLMExtractor  # anthropic が要るので遅延インポート

    llm = LLMExtractor(config.llm, config.detection)
    if config.extractor == "llm":
        return llm
    return AutoExtractor(rules, llm)
