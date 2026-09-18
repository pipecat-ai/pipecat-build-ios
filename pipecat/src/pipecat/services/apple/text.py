"""Plain speech cleanup after Pipecat's Markdown text filter."""

import re

from pipecat.utils.text.base_text_filter import BaseTextFilter


class PlainSpeechTextFilter(BaseTextFilter):
    """Remove residual presentation markers from complete spoken sentences.

    Use after ``MarkdownTextFilter`` in a TTS service's ``text_filters``. The
    standard filter handles Markdown/HTML; this enforces the voice-only surface
    for malformed emphasis, Unicode bullets, and numbered-list prefixes.
    """

    async def filter(self, text: str) -> str:
        """Clean a sentence while preserving ordinary spoken punctuation.

        Args:
            text: Sentence after standard Markdown filtering.

        Returns:
            Plain speech with layout markers removed.
        """
        text = re.sub(r"(?m)^\s*(?:[-+•◦▪●]+|\d+[.)])(?:\s+|$)", "", text)
        text = re.sub(r"[•◦▪●]", "; ", text)
        comparisons = {
            "<": " less than ",
            ">": " greater than ",
            "<=": " less than or equal to ",
            ">=": " greater than or equal to ",
        }
        text = re.sub(
            r"(?<=\d)\s*(<=|>=|<|>)\s*(?=[+-]?\d)",
            lambda match: comparisons[match.group(1)],
            text,
        )
        text = re.sub(r"[<>]", " ", text)
        text = re.sub(r"[*`_#]", "", text)
        return re.sub(r"\s+", " ", text).strip()
