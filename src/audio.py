"""Module C: Generate TTS audio with per-sentence timestamps via edge-tts."""

import re
import logging

import edge_tts

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"
DEFAULT_RATE = "-10%"
EDGE_TTS_BITRATE_KBPS = 48


def _mp3_chunk_duration_ms(chunk: bytes) -> float:
    """Estimate MP3 chunk duration from byte length (edge-tts outputs 48kbps)."""
    return (len(chunk) * 8) / EDGE_TTS_BITRATE_KBPS


def split_into_sentences(paragraphs: list[str]) -> list[dict]:
    """Split paragraphs into individual sentences, tracking paragraph indices."""
    sentences = []
    for para_idx, para in enumerate(paragraphs):
        parts = re.split(r'(?<=[.!?])\s+', para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append({"text": part, "paragraph_index": para_idx})
    return sentences


async def generate_audio(
    paragraphs: list[str],
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
) -> tuple[bytes, dict]:
    """
    Generate a single MP3 from all paragraphs and return (audio_bytes, timeline).
    Timeline contains per-sentence start/end times in milliseconds.
    """
    sentences = split_into_sentences(paragraphs)
    logger.info("Generating audio for %d sentences…", len(sentences))

    timeline_entries = []
    audio_chunks = []
    cumulative_ms = 0.0

    for i, sentence_info in enumerate(sentences):
        text = sentence_info["text"]
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate)

        chunk_audio = b""
        word_end_ms = 0.0

        async for event in communicate.stream():
            if event["type"] == "audio":
                chunk_audio += event["data"]
            elif event["type"] == "WordBoundary":
                end = (event["offset"] + event["duration"]) / 10_000
                word_end_ms = max(word_end_ms, end)

        chunk_duration = _mp3_chunk_duration_ms(chunk_audio)

        timeline_entries.append({
            "index": i,
            "text": text,
            "start_ms": round(cumulative_ms),
            "end_ms": round(cumulative_ms + chunk_duration),
            "paragraph_index": sentence_info["paragraph_index"],
        })

        audio_chunks.append(chunk_audio)
        cumulative_ms += chunk_duration

    full_audio = b"".join(audio_chunks)
    timeline = {
        "sentences": timeline_entries,
        "total_duration_ms": round(cumulative_ms),
    }

    logger.info(
        "Audio generated: %d bytes, %.1f seconds",
        len(full_audio), cumulative_ms / 1000,
    )
    return full_audio, timeline
