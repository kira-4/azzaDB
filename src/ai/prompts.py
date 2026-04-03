from pydantic import BaseModel, Field


SYSTEM_PROMPT = """You are a metadata extractor for Shia Islamic audio recordings (latmyaat) scraped from Telegram.
Extract structured metadata from the Arabic message text provided.
Return ONLY valid JSON matching the schema — no commentary, no markdown fences.

Fields:
- album_type: "شريط" or "إصدار" (or null if unclear)
- album_name: the title inside [] brackets, or null
- artists: list of artist names (strip # symbols if present), or []
- occasion: the Islamic occasion (e.g. استشهاد فاطمة الزهراء), or null
- hijri_date: full year string like "1439 هـ", or null
- hijri_month: month name in Arabic, or null
- hijri_day: day/night description like "ليلة 13", or null
- location: full location string, or null
- city: city name only, or null
- audio_engineer: name of audio engineer if mentioned, or null
- recording_engineer: name of recording engineer if mentioned, or null
- notes: any additional notes (venue/mosque name), or null
- confidence: float 0.0–1.0 reflecting your confidence in the extraction
"""

USER_PROMPT_TEMPLATE = "Extract metadata from this Arabic Telegram message:\n\n{raw_text}"


class ExtractionResult(BaseModel):
    album_type: str | None = None
    album_name: str | None = None
    artists: list[str] = Field(default_factory=list)
    occasion: str | None = None
    hijri_date: str | None = None
    hijri_month: str | None = None
    hijri_day: str | None = None
    location: str | None = None
    city: str | None = None
    audio_engineer: str | None = None
    recording_engineer: str | None = None
    notes: str | None = None
    confidence: float = 0.0
