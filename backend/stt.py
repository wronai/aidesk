"""
Real-time speech-to-text using Deepgram Nova-3.
"""
import os
import asyncio
from typing import Callable, Optional
import structlog
from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveTranscriptionEvents,
    LiveOptions,
)
import sounddevice as sd
import numpy as np

logger = structlog.get_logger()


class RealtimeSTT:
    """
    Real-time speech-to-text using Deepgram streaming API.
    """

    def __init__(
        self,
        api_key: str,
        language: str = "pl",
        model: str = "nova-3",
        input_device: Optional[str] = None,
        monitor_device: Optional[str] = None,
    ):
        """
        Initialize STT.

        Args:
            api_key: Deepgram API key
            language: Language code (pl, en, etc.)
            model: Deepgram model (nova-3, nova-2, etc.)
            input_device: PulseAudio/PipeWire source name for microphone (None = default)
            monitor_device: PulseAudio/PipeWire monitor source for speaker loopback (None = disabled)
        """
        self.api_key = api_key
        self.language = language
        self.model = model
        self.input_device = input_device or None
        self.monitor_device = monitor_device or None
        self.transcript_callback: Optional[Callable] = None
        self.connection = None
        self.is_running = False
        self.total_duration = 0.0
        self.total_cost = 0.0

        config = DeepgramClientOptions(options={"keepalive": "true"})
        self.client = DeepgramClient(api_key, config)

        # Resolve PulseAudio/PipeWire device name → sounddevice index
        self._sd_device_index = self._resolve_device(self.input_device)
        self._sd_monitor_index = self._resolve_device(self.monitor_device)

        logger.info(
            "Deepgram STT initialized",
            language=language,
            model=model,
            input_device=self.input_device,
            monitor_device=self.monitor_device,
            sd_device_index=self._sd_device_index,
            sd_monitor_index=self._sd_monitor_index,
        )

    async def start(self, on_transcript: Callable):
        """
        Start streaming STT.

        Args:
            on_transcript: Async callback(text: str, is_final: bool)
        """
        if self.is_running:
            logger.warning("STT already running")
            return

        self.transcript_callback = on_transcript
        self.is_running = True

        try:
            # Create connection
            self.connection = self.client.listen.asyncwebsocket.v("1")

            # Register event handlers
            async def on_message(self_conn, result, **kwargs):
                sentence = result.channel.alternatives[0].transcript
                if sentence:
                    is_final = result.is_final
                    if self.transcript_callback:
                        await self.transcript_callback(sentence, is_final)

                    # Track usage
                    if is_final:
                        duration = result.duration if hasattr(result, 'duration') else 1.0
                        self.total_duration += duration
                        # Nova-3 pricing: $0.0077/minute streaming
                        cost = (duration / 60.0) * 0.0077
                        self.total_cost += cost

            async def on_error(self_conn, error, **kwargs):
                logger.error("Deepgram error", error=str(error))

            async def on_close(self_conn, close_event, **kwargs):
                logger.info("Deepgram connection closed")

            self.connection.on(LiveTranscriptionEvents.Transcript, on_message)
            self.connection.on(LiveTranscriptionEvents.Error, on_error)
            self.connection.on(LiveTranscriptionEvents.Close, on_close)

            # Configure streaming options
            options = LiveOptions(
                model=self.model,
                language=self.language,
                encoding="linear16",
                sample_rate=16000,
                channels=1,
                interim_results=True,
                smart_format=True,
                endpointing=300,  # ms of silence before finalizing
                punctuate=True,
                utterance_end_ms=1000,
            )

            # Start connection
            if await self.connection.start(options):
                logger.info("Deepgram connection started")
                # Start audio capture
                asyncio.create_task(self._capture_audio())
            else:
                logger.error("Failed to start Deepgram connection")
                self.is_running = False

        except Exception as e:
            logger.error("Failed to start STT", error=str(e))
            self.is_running = False
            raise

    @staticmethod
    def _resolve_device(pulse_name: Optional[str]) -> Optional[int]:
        """
        Resolve PulseAudio/PipeWire device name to sounddevice index.
        Returns None if not found or not specified.
        """
        if not pulse_name:
            return None
        try:
            devices = sd.query_devices()
            for i, d in enumerate(devices):
                if pulse_name in d.get("name", ""):
                    return i
            logger.warning("Audio device not found in sounddevice", pulse_name=pulse_name)
        except Exception as e:
            logger.warning("Failed to resolve audio device", pulse_name=pulse_name, error=str(e))
        return None

    async def _capture_audio(self):
        """Capture audio from microphone (and optionally monitor) and stream to Deepgram."""

        def audio_callback(indata, frames, time_info, status):
            """Called for each audio block from sounddevice."""
            if status:
                logger.warning("Audio callback status", status=status)

            if self.connection and self.is_running:
                # Convert float32 to int16
                audio_data = (indata * 32767).astype(np.int16)
                audio_bytes = audio_data.tobytes()

                # Send to Deepgram
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.connection.send(audio_bytes),
                        asyncio.get_event_loop(),
                    )
                except Exception as e:
                    logger.error("Failed to send audio", error=str(e))

        # Determine which device to use: monitor (loopback) takes priority if set
        device_index = self._sd_monitor_index or self._sd_device_index
        device_label = self.monitor_device or self.input_device or "default"

        try:
            logger.info("Starting audio capture", device=device_label, device_index=device_index)

            stream_kwargs = dict(
                samplerate=16000,
                channels=1,
                dtype="float32",
                blocksize=4000,
                callback=audio_callback,
            )
            if device_index is not None:
                stream_kwargs["device"] = device_index

            with sd.InputStream(**stream_kwargs):
                while self.is_running:
                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.error("Audio capture failed", error=str(e), device=device_label)
            self.is_running = False

    async def stop(self):
        """Stop streaming STT."""
        if not self.is_running:
            return

        self.is_running = False

        if self.connection:
            try:
                await self.connection.finish()
                logger.info(
                    "Deepgram stopped",
                    total_duration_min=round(self.total_duration / 60, 2),
                    total_cost_usd=round(self.total_cost, 4),
                )
            except Exception as e:
                logger.error("Error closing Deepgram", error=str(e))

        self.connection = None

    def get_stats(self) -> dict:
        """Get STT statistics."""
        return {
            "is_running": self.is_running,
            "language": self.language,
            "model": self.model,
            "input_device": self.input_device or "(default)",
            "monitor_device": self.monitor_device or "(disabled)",
            "total_duration_min": round(self.total_duration / 60, 2),
            "total_cost_usd": round(self.total_cost, 4),
        }


def create_stt_from_env() -> Optional[RealtimeSTT]:
    """Create STT instance from environment variables."""
    from dotenv import load_dotenv

    load_dotenv()

    if os.getenv("ENABLE_STT", "true").lower() != "true":
        logger.info("STT disabled by config")
        return None

    api_key = os.getenv("DEEPGRAM_API_KEY")
    if not api_key:
        logger.warning("DEEPGRAM_API_KEY not set, STT disabled")
        return None

    return RealtimeSTT(
        api_key=api_key,
        language=os.getenv("STT_LANGUAGE", "pl"),
        model=os.getenv("DEEPGRAM_MODEL", "nova-3"),
        input_device=os.getenv("STT_INPUT_DEVICE", ""),
        monitor_device=os.getenv("STT_MONITOR_DEVICE", ""),
    )
