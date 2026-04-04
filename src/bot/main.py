import logging

from telegram.ext import AIORateLimiter, Application

from src.config import TELEGRAM_BOT_TOKEN
from src.bot.handlers.verification_handler import build_verification_conversation
from src.bot.handlers.admin_handler import build_admin_handlers
from src.bot.handlers.prescreen_handler import build_prescreen_handler

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_app() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).rate_limiter(AIORateLimiter()).build()

    for handler in build_admin_handlers():
        app.add_handler(handler)

    app.add_handler(build_prescreen_handler())
    app.add_handler(build_verification_conversation())

    return app


def run():
    app = build_app()
    logger.info("Verification bot starting…")
    app.run_polling()
