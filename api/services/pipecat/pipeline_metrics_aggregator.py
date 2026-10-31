import time
from collections import defaultdict
from typing import Dict, Optional

from loguru import logger

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    MetricsFrame,
    StartFrame,
)
from pipecat.metrics.metrics import (
    LLMTokenUsage,
    LLMUsageMetricsData,
    TTSUsageMetricsData,
)

# STTUsageMetricsData was added in a newer pipecat version (upstream commit
# 2a8de52bb). The Dograh fork hasn't merged it yet. Define a placeholder class
# so the isinstance() check compiles but NEVER matches any frame — STT metrics
# are simply not tracked until the fork merges upstream.
try:
    from pipecat.metrics.metrics import STTUsageMetricsData
except ImportError:

    class STTUsageMetricsData:  # type: ignore[no-redef]
        """Placeholder — pipecat fork doesn't have this class yet."""

        pass
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class PipelineLLMTokenUsage(LLMTokenUsage):
    """Subclass of LLMTokenUsage supporting optional speech-to-speech modality-split tokens."""

    prompt_text_tokens: Optional[int] = None
    prompt_audio_tokens: Optional[int] = None
    completion_text_tokens: Optional[int] = None
    completion_audio_tokens: Optional[int] = None


class PipelineMetricsAggregator(FrameProcessor):
    def __init__(self):
        super().__init__()
        # Structure: {f"{processor}|||{model}": aggregated_metrics}
        # For LLM: aggregated_metrics is LLMTokenUsage
        # For TTS: aggregated_metrics is int (total characters)
        # For STT: aggregated_metrics is float (total seconds)

        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None
        self._llm_usage_metrics: Dict[str, LLMTokenUsage] = {}
        self._tts_usage_metrics: Dict[str, int] = defaultdict(int)
        self._stt_usage_metrics: Dict[str, float] = defaultdict(float)
        # Embedding tokens from RAG calls (summed across all retrievals in a call)
        self._embedding_tokens: int = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self._start(frame)
        elif isinstance(frame, EndFrame):
            await self._stop(frame)
        elif isinstance(frame, CancelFrame):
            await self._cancel(frame)
        elif isinstance(frame, MetricsFrame):
            for data in frame.data:
                if isinstance(data, LLMUsageMetricsData):
                    await self._handle_llm_usage_metrics(data)
                elif isinstance(data, TTSUsageMetricsData):
                    await self._handle_tts_usage_metrics(data)
                elif isinstance(data, STTUsageMetricsData):
                    await self._handle_stt_usage_metrics(data)

        await self.push_frame(frame, direction)

    async def _start(self, _: StartFrame):
        """Start tracking call duration."""
        self._start_time = time.time()
        self._stop_time = None

    async def _stop(self, _: EndFrame):
        """Stop tracking call duration."""
        if self._start_time is not None and self._stop_time is None:
            self._stop_time = time.time()

    async def _cancel(self, _: CancelFrame):
        """Handle call cancellation - also stop tracking duration."""
        if self._start_time is not None and self._stop_time is None:
            self._stop_time = time.time()

    async def _handle_llm_usage_metrics(self, data: LLMUsageMetricsData):
        key = f"{data.processor}|||{data.model}"
        new_usage = data.value

        def _sum_optional(a, b):
            # None + None stays None (modality not reported); otherwise sum,
            # treating a missing side as 0 so a mixed stream still accumulates.
            if a is None and b is None:
                return None
            return (a or 0) + (b or 0)

        new_prompt_text = getattr(new_usage, "prompt_text_tokens", None)
        new_prompt_audio = getattr(new_usage, "prompt_audio_tokens", None)
        new_completion_text = getattr(new_usage, "completion_text_tokens", None)
        new_completion_audio = getattr(new_usage, "completion_audio_tokens", None)

        if key in self._llm_usage_metrics:
            # Aggregate with existing metrics
            existing = self._llm_usage_metrics[key]
            aggregated = PipelineLLMTokenUsage(
                prompt_tokens=existing.prompt_tokens + new_usage.prompt_tokens,
                completion_tokens=existing.completion_tokens
                + new_usage.completion_tokens,
                total_tokens=existing.total_tokens + new_usage.total_tokens,
                cache_read_input_tokens=(getattr(existing, "cache_read_input_tokens", 0) or 0)
                + (getattr(new_usage, "cache_read_input_tokens", 0) or 0),
                cache_creation_input_tokens=(getattr(existing, "cache_creation_input_tokens", 0) or 0)
                + (getattr(new_usage, "cache_creation_input_tokens", 0) or 0),
                prompt_text_tokens=_sum_optional(
                    getattr(existing, "prompt_text_tokens", None), new_prompt_text
                ),
                prompt_audio_tokens=_sum_optional(
                    getattr(existing, "prompt_audio_tokens", None), new_prompt_audio
                ),
                completion_text_tokens=_sum_optional(
                    getattr(existing, "completion_text_tokens", None), new_completion_text
                ),
                completion_audio_tokens=_sum_optional(
                    getattr(existing, "completion_audio_tokens", None), new_completion_audio
                ),
            )
            self._llm_usage_metrics[key] = aggregated
        else:
            # First occurrence for this processor+model combination
            self._llm_usage_metrics[key] = PipelineLLMTokenUsage(
                prompt_tokens=new_usage.prompt_tokens,
                completion_tokens=new_usage.completion_tokens,
                total_tokens=new_usage.total_tokens,
                cache_read_input_tokens=getattr(new_usage, "cache_read_input_tokens", None),
                cache_creation_input_tokens=getattr(new_usage, "cache_creation_input_tokens", None),
                prompt_text_tokens=new_prompt_text,
                prompt_audio_tokens=new_prompt_audio,
                completion_text_tokens=new_completion_text,
                completion_audio_tokens=new_completion_audio,
            )

        logger.debug(f"LLM usage metrics: {self._llm_usage_metrics}")

    async def _handle_tts_usage_metrics(self, data: TTSUsageMetricsData):
        key = f"{data.processor}|||{data.model}"
        self._tts_usage_metrics[key] += data.value
        # logger.debug(f"TTS usage metrics: {self._tts_usage_metrics}")

    async def _handle_stt_usage_metrics(self, data: STTUsageMetricsData):
        key = f"{data.processor}|||{data.model}"
        self._stt_usage_metrics[key] += data.value
        logger.debug(f"STT usage metrics: {self._stt_usage_metrics}")

    def add_embedding_tokens(self, tokens: int) -> None:
        """Accumulate embedding tokens from a RAG retrieval call.

        Called by the engine each time retrieve_from_knowledge_base returns
        so the total is available in the final usage_info.
        """
        self._embedding_tokens += int(tokens or 0)
        logger.debug(f"Embedding tokens accumulated: {self._embedding_tokens} (+{tokens})")

    def get_llm_usage_metrics(self) -> Dict[str, LLMTokenUsage]:
        """Get the aggregated LLM usage metrics grouped by processor|||model."""
        return self._llm_usage_metrics

    def get_tts_usage_metrics(self) -> Dict[str, int]:
        """Get the aggregated TTS usage metrics grouped by processor|||model."""
        return self._tts_usage_metrics

    def get_stt_usage_metrics(self) -> Dict[str, float]:
        """Get the aggregated STT usage metrics grouped by processor|||model."""
        return self._stt_usage_metrics

    def get_call_duration(self) -> float:
        """Get call duration"""
        if self._start_time is None:
            return 0.0

        if self._stop_time is None:
            call_duration = time.time() - self._start_time
        else:
            call_duration = self._stop_time - self._start_time

        # Lets return a rounded integer
        return int(round(call_duration))

    def get_all_usage_metrics_serialized(self) -> Dict[str, Dict[str, any]]:
        """Get all aggregated usage metrics in JSON-serializable format."""
        serialized_llm = {}
        for key, usage in self._llm_usage_metrics.items():
            serialized_llm[key] = {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
                # Per-modality split for speech-to-speech billing (None when the
                # model didn't report it — i.e. an ordinary text LLM).
                "prompt_text_tokens": getattr(usage, "prompt_text_tokens", None),
                "prompt_audio_tokens": getattr(usage, "prompt_audio_tokens", None),
                "completion_text_tokens": getattr(usage, "completion_text_tokens", None),
                "completion_audio_tokens": getattr(usage, "completion_audio_tokens", None),
            }

        return {
            "llm": serialized_llm,
            "tts": dict(self._tts_usage_metrics),
            "stt": dict(self._stt_usage_metrics),
            "embedding_tokens": self._embedding_tokens,
            "call_duration_seconds": self.get_call_duration(),
        }

    def reset_metrics(self):
        """Reset all aggregated metrics."""
        self._llm_usage_metrics.clear()
        self._tts_usage_metrics.clear()
        self._stt_usage_metrics.clear()
        self._embedding_tokens = 0
        self._start_time = None
        self._stop_time = None
