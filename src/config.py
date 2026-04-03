import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
TARGET_GROUP_ID = int(os.environ["TARGET_GROUP_ID"])

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
VERIFICATION_CHAT_ID = int(os.environ["VERIFICATION_CHAT_ID"])
ADMIN_USER_IDS = [int(x) for x in os.environ["ADMIN_USER_IDS"].split(",")]

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

DATABASE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "azzadb.sqlite")
DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "downloads")
COVERS_DIR = os.path.join(DOWNLOADS_DIR, "covers")
AUDIO_DIR = os.path.join(DOWNLOADS_DIR, "audio")

AI_CONFIDENCE_THRESHOLD = 0.7
