import json
import logging

from google import genai
from google.genai import types

from src.config import GEMINI_API_KEY, AI_CONFIDENCE_THRESHOLD
from src.ai.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, ExtractionResult
from src.database.db import log_ai_extraction, update_album_ai_fields

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.0-flash"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    response_mime_type="application/json",
    temperature=0.1,
)


async def extract_metadata(album_id: int, raw_text: str) -> ExtractionResult:
    """Call Gemini to extract metadata for an album, persist results to DB."""
    client = _get_client()
    prompt = USER_PROMPT_TEMPLATE.format(raw_text=raw_text)

    try:
        response = await client.aio.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=_CONFIG,
        )
        raw_json = response.text
    except Exception as e:
        logger.error("Gemini API error for album %d: %s", album_id, e)
        raise

    log_ai_extraction(album_id, MODEL_NAME, raw_json)

    try:
        data = json.loads(raw_json)
        result = ExtractionResult(**data)
    except Exception as e:
        logger.error("Failed to parse Gemini response for album %d: %s\n%s", album_id, e, raw_json)
        raise

    v_status = "needs_review" if result.confidence < AI_CONFIDENCE_THRESHOLD else "pending"

    update_album_ai_fields(album_id, {
        "album_type": result.album_type,
        "album_name_ar": result.album_name,
        "occasion_ar": result.occasion,
        "hijri_date": result.hijri_date,
        "hijri_month": result.hijri_month,
        "hijri_day": result.hijri_day,
        "location_ar": result.location,
        "city_ar": result.city,
        "audio_engineer": result.audio_engineer,
        "recording_engineer": result.recording_engineer,
        "notes_ar": result.notes,
        "ai_confidence": result.confidence,
        "ai_extracted": 1,
        "verification_status": v_status,
    })

    logger.info("Album %d extracted (confidence=%.2f, status=%s)", album_id, result.confidence, v_status)
    return result
