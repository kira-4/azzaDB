import json
import logging

import google.generativeai as genai

from src.config import GEMINI_API_KEY, AI_CONFIDENCE_THRESHOLD
from src.ai.prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE, ExtractionResult
from src.database.db import log_ai_extraction, update_album_ai_fields

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.0-flash"


def _configure():
    genai.configure(api_key=GEMINI_API_KEY)


def _get_model() -> genai.GenerativeModel:
    _configure()
    return genai.GenerativeModel(
        model_name=MODEL_NAME,
        system_instruction=SYSTEM_PROMPT,
        generation_config=genai.GenerationConfig(
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )


async def extract_metadata(album_id: int, raw_text: str) -> ExtractionResult:
    """Call Gemini to extract metadata for an album, persist results to DB."""
    model = _get_model()
    prompt = USER_PROMPT_TEMPLATE.format(raw_text=raw_text)

    try:
        response = model.generate_content(prompt)
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

    # Determine verification status based on confidence
    if result.confidence < AI_CONFIDENCE_THRESHOLD:
        v_status = "needs_review"
    else:
        v_status = "pending"

    fields: dict = {
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
    }
    update_album_ai_fields(album_id, fields)

    logger.info(
        "Album %d extracted (confidence=%.2f, status=%s)", album_id, result.confidence, v_status
    )
    return result
