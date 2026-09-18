#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Simple text aggregator for basic sentence-boundary text processing.

This module provides a straightforward text aggregator that accumulates text
until it finds an end-of-sentence marker, making it suitable for basic TTS
text processing scenarios.
"""

from collections.abc import AsyncIterator, Callable

from pipecat.utils.string import SENTENCE_ENDING_PUNCTUATION, match_endofsentence
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType, BaseTextAggregator


class SimpleTextAggregator(BaseTextAggregator):
    """Simple text aggregator that accumulates text until sentence boundaries.

    This aggregator provides basic functionality for accumulating text tokens
    and releasing them when an end-of-sentence marker is detected. It's the
    most straightforward implementation of text aggregation for TTS processing.
    """

    def __init__(
        self,
        *,
        sentence_boundary_matcher: Callable[[str], int] | None = None,
        max_buffer_chars: int | None = None,
        **kwargs,
    ):
        """Initialize the simple text aggregator.

        Creates an empty text buffer ready to begin accumulating text tokens.

        Args:
            sentence_boundary_matcher: Returns the first complete sentence's end
                offset, or zero when more text is needed. Defaults to Pipecat's
                NLTK matcher. Native hosts can supply their platform tokenizer.
            max_buffer_chars: Optional upper bound on buffered characters. Long
                sentences are released at a space, or at the limit for an
                uninterrupted token. None keeps complete sentences buffered.
            **kwargs: Additional arguments passed to BaseTextAggregator (e.g. aggregation_type).
        """
        super().__init__(**kwargs)
        if max_buffer_chars is not None and max_buffer_chars < 1:
            raise ValueError("max_buffer_chars must be positive")
        self._sentence_boundary_matcher = sentence_boundary_matcher or match_endofsentence
        self._max_buffer_chars = max_buffer_chars
        self._text = ""
        self._needs_lookahead: bool = False

    @property
    def text(self) -> Aggregation:
        """Get the currently aggregated text.

        Returns:
            The text that has been accumulated in the buffer.
        """
        return Aggregation(text=self._text.strip(" "), type=AggregationType.SENTENCE)

    async def aggregate(self, text: str) -> AsyncIterator[Aggregation]:
        """Aggregate text and yield completed aggregations.

        In SENTENCE mode, processes the input text character-by-character. When
        sentence-ending punctuation is detected, it waits for non-whitespace
        lookahead before checking the sentence boundary.

        In TOKEN mode, yields the text immediately without buffering.

        Args:
            text: Text to aggregate.

        Yields:
            Aggregation objects (sentences in SENTENCE mode, tokens in TOKEN mode).
        """
        if self._aggregation_type == AggregationType.TOKEN:
            if text:
                yield Aggregation(text=text, type=AggregationType.TOKEN)
            return

        # Process text character by character
        for char in text:
            self._text += char

            # Check for sentence with lookahead
            result = await self._check_sentence_with_lookahead(char)
            if result:
                yield result
            if self._max_buffer_chars and len(self._text) >= self._max_buffer_chars:
                end = self._text.rfind(" ", 0, self._max_buffer_chars + 1)
                if end < 1:
                    end = self._max_buffer_chars
                result, self._text = self._text[:end], self._text[end:]
                self._needs_lookahead = bool(
                    self._text and self._text[-1] in SENTENCE_ENDING_PUNCTUATION
                )
                if result.strip():
                    yield Aggregation(text=result.strip(), type=AggregationType.SENTENCE)

    async def _check_sentence_with_lookahead(self, char: str) -> Aggregation | None:
        """Check for sentence boundaries using lookahead logic.

        This method implements the core sentence detection logic with lookahead.
        When sentence-ending punctuation is detected, it waits for the next
        non-whitespace character before checking the sentence boundary. This disambiguates cases
        like "$29." (not a sentence) vs "$29. Next" (sentence ends at period).
        Whitespace alone is not meaningful lookahead since it appears in both
        cases. Instead, the first non-whitespace character after the punctuation
        is used to confirm the sentence boundary.

        Subclasses can call this via super() to reuse the lookahead behavior
        while adding their own logic (e.g., tag handling, pattern matching).

        Args:
            char: The most recently added character (used for lookahead check).

        Returns:
            Aggregation if sentence found, None otherwise.
        """
        # If we need lookahead, check if we now have non-whitespace
        if self._needs_lookahead:
            # Check if the new character is non-whitespace
            if char.strip():
                # We have meaningful lookahead, check the sentence boundary
                self._needs_lookahead = False
                eos_marker = self._sentence_boundary_matcher(self._text)
                if not 0 <= eos_marker <= len(self._text):
                    raise ValueError("Sentence boundary offset is outside the buffered text")

                if eos_marker:
                    # The matcher confirmed a sentence - return it
                    result = self._text[:eos_marker]
                    self._text = self._text[eos_marker:]
                    return Aggregation(text=result.strip(" "), type=AggregationType.SENTENCE)
                # No sentence found - keep accumulating
                return None
            # Still whitespace, keep waiting
            return None

        # Check if we just added sentence-ending punctuation
        if self._text and self._text[-1] in SENTENCE_ENDING_PUNCTUATION:
            # Mark that we need lookahead (don't call the matcher yet)
            self._needs_lookahead = True

        return None

    async def flush(self) -> Aggregation | None:
        """Flush any remaining text in the buffer.

        Returns any text remaining in the buffer. This is called at the end
        of a stream to ensure all text is processed. In TOKEN mode, returns
        None since tokens are yielded immediately.

        Returns:
            Any remaining text as a sentence, or None if buffer is empty or in TOKEN mode.
        """
        if self._aggregation_type == AggregationType.TOKEN:
            return None

        if self._text:
            # Return whatever we have in the buffer
            result = self._text
            await self.reset()
            return Aggregation(text=result.strip(" "), type=AggregationType.SENTENCE)
        return None

    async def handle_interruption(self):
        """Handle interruptions by clearing the text buffer.

        Called when an interruption occurs in the processing pipeline,
        discarding any partially accumulated text.
        """
        self._text = ""
        self._needs_lookahead = False

    async def reset(self):
        """Clear the internally aggregated text.

        Resets the aggregator to its initial empty state, discarding
        any accumulated text content.
        """
        self._text = ""
        self._needs_lookahead = False
