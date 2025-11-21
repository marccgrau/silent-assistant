#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""silent-assistant - Silent Advisor Bot

This bot listens to a two-human conversation, performs diarized STT with Deepgram,
generates agent-facing advice after each customer utterance, emits advice via RTVI
as JSON, and logs utterances/advice/audio to Supabase.
"""

import asyncio
import copy
import datetime
import io
import json
import os
import uuid
import wave
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for older runtimes
    import tomli as tomllib  # type: ignore

import aiofiles
from dotenv import load_dotenv
from loguru import logger
from openai import AsyncOpenAI
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.processors.frameworks.rtvi import (
    RTVIObserver,
    RTVIObserverParams,
    RTVIProcessor,
    RTVIServerMessageFrame,
)
from pipecat.runner.types import DailyRunnerArguments, RunnerArguments, SmallWebRTCRunnerArguments
from pipecat.services.deepgram.flux.stt import DeepgramFluxSTTService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from supabase import Client, create_client

load_dotenv(override=True)

DEFAULT_ADVISOR_PROMPT = (
    "You are a concise call coach for a customer service agent. "
    "Listen to the conversation transcript and, after each customer turn, provide short, "
    "actionable guidance for the agent. Keep advice tactful, specific, and focused on next steps. "
    "Return JSON with keys 'advice' (1-2 sentences) and 'rationale' (optional, <=1 sentence). "
    "Do not include greetings or asterisks."
)

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_PROMPT_PATH = BASE_DIR / "prompts" / "advisor_system_prompt.txt"
DEFAULT_CONFIG_PATH = BASE_DIR / "config.toml"
DEFAULT_CONFIG: Dict[str, Any] = {
    "advisor": {
        "model": "gpt-5-mini-2025-08-07",
        "prompt_file": str(DEFAULT_PROMPT_PATH),
    },
    "deepgram": {
        "model": "nova-3-general",
        "diarize": True,
        "paragraphs": True,
        "punctuate": True,
        "smart_format": True,
    },
    "supabase": {
        "url": "",
        "bucket": "call-audio",
        "audio_folder": "calls",
    },
    "participants": {
        "customer_speaker_label": "",
        "agent_speaker_label": "",
        "agent_id": "",
        "customer_id": "",
    },
}


def merge_config(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two config dictionaries."""
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = merge_config(base.get(key, {}), value)
        else:
            base[key] = value
    return base


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load non-secret settings from a TOML config file."""
    path = Path(config_path or os.getenv("BOT_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if not path.exists():
        logger.info(f"Config file not found at {path}; using defaults.")
        return cfg

    try:
        with path.open("rb") as f:
            loaded = tomllib.load(f)
            cfg = merge_config(cfg, loaded)
            logger.info(f"Loaded config from {path}")
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Failed to read config at {path}: {exc}")
    return cfg


def load_prompt(path: Optional[str]) -> str:
    """Load advisor prompt text from file, fallback to default string."""
    if path:
        prompt_path = Path(path)
        if not prompt_path.is_absolute():
            prompt_path = BASE_DIR / prompt_path
        try:
            return prompt_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            logger.warning(f"Prompt file not found at {prompt_path}; using default prompt.")
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(f"Failed to read prompt file {prompt_path}: {exc}")
    return DEFAULT_ADVISOR_PROMPT


class InputAudioRecorder(FrameProcessor):
    """Lightweight recorder that buffers incoming raw audio frames."""

    def __init__(self):
        super().__init__()
        self.reset()

    def reset(self):
        self._buffer = bytearray()
        self.sample_rate: Optional[int] = None
        self.num_channels: Optional[int] = None

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            if self.sample_rate is None:
                self.sample_rate = frame.sample_rate
            self.num_channels = getattr(frame, "num_channels", None) or self.num_channels or 1
            self._buffer.extend(frame.audio)
        await self.push_frame(frame, direction)

    def export(self) -> Tuple[bytes, int, int]:
        audio = bytes(self._buffer)
        return audio, self.sample_rate or 16000, self.num_channels or 1


class TranscriptionRouter(FrameProcessor):
    """Intercepts transcription frames to route interim/final handling."""

    def __init__(self, on_final=None, on_interim=None):
        super().__init__()
        self._on_final = on_final
        self._on_interim = on_interim

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, InterimTranscriptionFrame) and self._on_interim:
            await self._on_interim(frame)
        elif isinstance(frame, TranscriptionFrame) and self._on_final:
            await self._on_final(frame)

        await self.push_frame(frame, direction)


async def save_audio_file(audio: bytes, filename: str, sample_rate: int, num_channels: int):
    """Persist audio to WAV on disk and return bytes."""
    if len(audio) == 0:
        return b""

    with io.BytesIO() as buffer:
        with wave.open(buffer, "wb") as wf:
            wf.setsampwidth(2)
            wf.setnchannels(num_channels)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)
        wav_bytes = buffer.getvalue()
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        async with aiofiles.open(filename, "wb") as file:
            await file.write(wav_bytes)
        logger.info(f"Audio saved to {filename}")
        return wav_bytes


def build_supabase_client(url: Optional[str], key: Optional[str]) -> Optional[Client]:
    if not url or not key:
        logger.warning("Supabase URL or service role key missing; persistence disabled.")
        return None
    try:
        return create_client(url, key)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Failed to initialize Supabase client: {exc}")
        return None


async def supabase_insert(client: Optional[Client], table: str, data: Dict[str, Any]):
    if not client:
        return
    try:
        await asyncio.to_thread(lambda: client.table(table).insert(data).execute())
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Supabase insert to {table} failed: {exc}")


async def supabase_update(
    client: Optional[Client], table: str, match_key: str, match_value: str, data: Dict[str, Any]
):
    if not client:
        return
    try:
        await asyncio.to_thread(
            lambda: client.table(table).update(data).eq(match_key, match_value).execute()
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Supabase update on {table} failed: {exc}")


async def supabase_upload_audio(
    client: Optional[Client], bucket: str, path: str, audio: bytes
) -> Optional[str]:
    if not client or not audio:
        return None
    try:

        def _upload():
            return client.storage.from_(bucket).upload(
                path, audio, {"content-type": "audio/wav", "upsert": True}
            )

        await asyncio.to_thread(_upload)

        def _public_url():
            res = client.storage.from_(bucket).get_public_url(path)
            if isinstance(res, dict):
                return res.get("publicUrl") or res.get("public_url")
            return res

        return await asyncio.to_thread(_public_url)
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Supabase upload failed: {exc}")
        return None


def extract_speaker_data(
    frame: TranscriptionFrame,
) -> Tuple[Optional[str], Any, Any, Dict[str, Any]]:
    """Pull diarization and timing info from a Deepgram result object."""
    result = getattr(frame, "result", None)
    speaker = getattr(frame, "user_id", None)
    start_ts = None
    end_ts = None
    metadata: Dict[str, Any] = {}

    def normalize_words(word_items):
        normalized = []
        for w in word_items or []:
            if isinstance(w, dict):
                normalized.append(
                    {
                        "word": w.get("word") or w.get("text"),
                        "start": w.get("start"),
                        "end": w.get("end"),
                        "confidence": w.get("confidence"),
                        "speaker": w.get("speaker"),
                    }
                )
            else:
                normalized.append(
                    {
                        "word": getattr(w, "word", None) or getattr(w, "text", None),
                        "start": getattr(w, "start", None),
                        "end": getattr(w, "end", None),
                        "confidence": getattr(w, "confidence", None),
                        "speaker": getattr(w, "speaker", None),
                    }
                )
        return normalized

    try:
        words = []
        # Deepgram SDK object shape
        if hasattr(result, "channel"):
            alternatives = getattr(result.channel, "alternatives", []) or []
            if alternatives:
                alt0 = alternatives[0]
                words_raw = getattr(alt0, "words", None) or []
                words = normalize_words(words_raw)
        # Dictionary shape fallback(s)
        if not words and isinstance(result, dict):
            alt_candidates = (
                result.get("channel", {}).get("alternatives")
                or result.get("alternatives")
                or []
            )
            if alt_candidates:
                alt0 = alt_candidates[0]
                words_raw = alt0.get("words") or []
                words = normalize_words(words_raw)

        if words:
            start_ts = words[0].get("start")
            end_ts = words[-1].get("end")
            if words[0].get("speaker") is not None:
                speaker = str(words[0].get("speaker"))
            metadata["words"] = words
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug(f"Failed to parse diarization metadata: {exc}")

    if result is not None and not metadata:
        try:
            if hasattr(result, "to_dict"):
                metadata["raw"] = result.to_dict()
            elif hasattr(result, "model_dump"):
                metadata["raw"] = result.model_dump()
            elif isinstance(result, dict):
                metadata["raw"] = result
        except Exception:
            metadata["raw"] = str(result)

    return speaker, start_ts, end_ts, metadata


def build_context_text(dialog: Deque[Dict[str, str]]) -> str:
    lines = [f"{entry['role'].capitalize()}: {entry['text']}" for entry in dialog]
    context = "\n".join(lines)
    if len(context) > 3000:
        return context[-3000:]
    return context


async def generate_advice(
    client: Optional[AsyncOpenAI],
    model: str,
    system_prompt: str,
    context_text: str,
    customer_text: str,
):
    if not client:
        logger.warning("OpenAI client missing; skipping advice generation.")
        return None

    try:
        # Prefer the latest OpenAI Responses API
        response = await client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Recent conversation:\n"
                        f"{context_text}\n\n"
                        f'Customer just said: "{customer_text}"\n'
                        "Provide concise agent-facing advice. Respond with JSON containing "
                        "`advice` and optional `rationale`."
                    ),
                },
            ],
            response_format={"type": "json_object"},
        )

        text = ""
        # Responses API returns output[].content[].text
        output_blocks = getattr(response, "output", []) or []
        if output_blocks:
            block = output_blocks[0]
            parts = getattr(block, "content", []) or []
            if parts and hasattr(parts[0], "text"):
                text = parts[0].text or ""
        if not text and hasattr(response, "choices"):  # fallback compatibility
            choices = getattr(response, "choices", []) or []
            if choices and getattr(choices[0], "message", None):
                text = choices[0].message.content or ""

        data = json.loads(text) if text else {}
        advice = data.get("advice") or text
        rationale = data.get("rationale", "")
        return {"advice": advice.strip(), "rationale": rationale.strip()}
    except Exception as exc:
        logger.warning(f"Responses API failed ({exc}); falling back to chat.completions.")
        try:  # pragma: no cover - fallback path
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": (
                            "Recent conversation:\n"
                            f"{context_text}\n\n"
                            f'Customer just said: "{customer_text}"\n'
                            "Provide concise agent-facing advice. Respond with JSON containing "
                            "`advice` and optional `rationale`."
                        ),
                    },
                ],
                response_format={"type": "json_object"},
            )
            content = resp.choices[0].message.content
            data = json.loads(content) if content else {}
            advice = data.get("advice") or content
            rationale = data.get("rationale", "")
            return {"advice": advice.strip(), "rationale": rationale.strip()}
        except Exception as exc2:  # pragma: no cover - defensive
            logger.error(f"Advice generation failed: {exc2}")
            return None


async def run_bot(transport: BaseTransport):
    logger.info("Starting silent advisor bot")

    config = load_config()
    advisor_cfg = config.get("advisor", {})
    deepgram_cfg = config.get("deepgram", {})
    supabase_cfg = config.get("supabase", {})
    participants_cfg = config.get("participants", {})

    advisor_model = advisor_cfg.get("model", DEFAULT_CONFIG["advisor"]["model"])
    advisor_prompt = load_prompt(
        advisor_cfg.get("prompt_file", DEFAULT_CONFIG["advisor"]["prompt_file"])
    )

    supabase_url = supabase_cfg.get("url") or os.getenv("SUPABASE_URL")
    supabase_bucket = supabase_cfg.get("bucket", DEFAULT_CONFIG["supabase"]["bucket"])
    supabase_audio_folder = supabase_cfg.get(
        "audio_folder", DEFAULT_CONFIG["supabase"]["audio_folder"]
    )
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    supabase = build_supabase_client(supabase_url, supabase_key)

    customer_label_hint = (participants_cfg.get("customer_speaker_label") or "").strip() or None
    agent_label_hint = (participants_cfg.get("agent_speaker_label") or "").strip() or None
    agent_id = (participants_cfg.get("agent_id") or "").strip() or None
    customer_id = (participants_cfg.get("customer_id") or "").strip() or None

    openai_client: Optional[AsyncOpenAI] = None
    openai_key = os.getenv("OPENAI_API_KEY", "")
    if openai_key:
        openai_client = AsyncOpenAI(api_key=openai_key)
    else:
        logger.warning("OPENAI_API_KEY not set; advice generation disabled.")

    deepgram_options = {
        "diarize": deepgram_cfg.get("diarize", True),
        "punctuate": deepgram_cfg.get("punctuate", True),
        "smart_format": deepgram_cfg.get("smart_format", True),
        "paragraphs": deepgram_cfg.get("paragraphs", True),
        "model": deepgram_cfg.get("model", DEFAULT_CONFIG["deepgram"]["model"]),
    }
    try:
        stt = DeepgramFluxSTTService(
            api_key=os.getenv("DEEPGRAM_API_KEY", ""), options=deepgram_options
        )
    except TypeError:
        logger.warning("DeepgramFluxSTTService does not accept 'options'; using defaults.")
        stt = DeepgramFluxSTTService(api_key=os.getenv("DEEPGRAM_API_KEY", ""))

    rtvi = RTVIProcessor()
    recorder = InputAudioRecorder()

    conversation_id = str(uuid.uuid4())
    conversation_started_at = datetime.datetime.utcnow().isoformat()
    utterance_seq = 0
    advice_seq = 0
    speaker_roles: Dict[str, str] = {}
    dialog: Deque[Dict[str, str]] = deque(maxlen=20)

    def resolve_role(label: Optional[str]) -> str:
        if label in speaker_roles:
            return speaker_roles[label]

        normalized = str(label) if label is not None else None
        if normalized and customer_label_hint and normalized == customer_label_hint:
            speaker_roles[normalized] = "customer"
            return "customer"
        if normalized and agent_label_hint and normalized == agent_label_hint:
            speaker_roles[normalized] = "agent"
            return "agent"

        if "customer" not in speaker_roles.values():
            if normalized:
                speaker_roles[normalized] = "customer"
            return "customer"

        if normalized:
            speaker_roles[normalized] = "agent"
            return "agent"

        return "unknown"

    async def handle_final_transcript(frame: TranscriptionFrame):
        nonlocal utterance_seq, advice_seq
        text = frame.text.strip()
        if not text:
            return

        speaker, start_ts, end_ts, metadata = extract_speaker_data(frame)
        role = resolve_role(speaker)
        utterance_seq += 1
        utterance_id = str(uuid.uuid4())

        dialog.append({"role": role, "text": text})

        await supabase_insert(
            supabase,
            "utterances",
            {
                "id": utterance_id,
                "conversation_id": conversation_id,
                "role": role,
                "text": text,
                "diarization_speaker": speaker,
                "start_ts": start_ts or frame.timestamp,
                "end_ts": end_ts or frame.timestamp,
                "seq": utterance_seq,
                "raw": metadata or None,
            },
        )

        if role != "customer":
            return

        advice_seq += 1
        context_text = build_context_text(dialog)
        advice_payload = await generate_advice(
            openai_client, advisor_model, advisor_prompt, context_text, text
        )
        if not advice_payload:
            return

        advice_id = str(uuid.uuid4())
        event_payload = {
            "type": "customer_advice",
            "conversation_id": conversation_id,
            "utterance_id": utterance_id,
            "customer_text": text,
            "speaker": "customer",
            "advice": advice_payload.get("advice", ""),
            "rationale": advice_payload.get("rationale", ""),
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "context_seq": advice_seq,
            "metadata": {
                "turn_start": start_ts or frame.timestamp,
                "turn_end": end_ts or frame.timestamp,
                "diarization_speaker": speaker,
            },
        }

        await rtvi.push_frame(RTVIServerMessageFrame(data=event_payload))
        logger.info(f"Advice emitted for utterance {utterance_id}")

        await supabase_insert(
            supabase,
            "advice",
            {
                "id": advice_id,
                "conversation_id": conversation_id,
                "utterance_id": utterance_id,
                "content": advice_payload.get("advice", ""),
                "rationale": advice_payload.get("rationale", ""),
                "created_at": datetime.datetime.utcnow().isoformat(),
                "metadata": event_payload["metadata"],
            },
        )

    transcript_router = TranscriptionRouter(on_final=handle_final_transcript)

    pipeline = Pipeline(
        [
            transport.input(),
            rtvi,
            recorder,
            stt,
            transcript_router,
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[
            RTVIObserver(
                rtvi,
                params=RTVIObserverParams(bot_llm_enabled=False, bot_tts_enabled=False),
            )
        ],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected; starting recording and transcript capture.")
        recorder.reset()
        await supabase_insert(
            supabase,
            "conversations",
            {
                "id": conversation_id,
                "started_at": conversation_started_at,
                "transport": transport.__class__.__name__,
                "agent_id": agent_id,
                "customer_id": customer_id,
            },
        )

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected; finalizing conversation.")
        audio, sample_rate, num_channels = recorder.export()
        timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"recordings/conversation_{conversation_id}_{timestamp}.wav"
        wav_bytes = await save_audio_file(audio, filename, sample_rate, num_channels)

        storage_path = f"{supabase_audio_folder}/{conversation_id}.wav"
        storage_url = await supabase_upload_audio(
            supabase, supabase_bucket, storage_path, wav_bytes
        )

        await supabase_update(
            supabase,
            "conversations",
            "id",
            conversation_id,
            {
                "ended_at": datetime.datetime.utcnow().isoformat(),
                "storage_url": storage_url,
            },
        )
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    transport = None

    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Pipecat Bot",
                params=DailyParams(
                    audio_in_enabled=True,
                    audio_out_enabled=False,
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
                    turn_analyzer=LocalSmartTurnAnalyzerV3(),
                    transcription_enabled=True,
                ),
            )
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection
            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_out_enabled=False,
                    vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
                    turn_analyzer=LocalSmartTurnAnalyzerV3(),
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
