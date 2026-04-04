"""
Colored logging formatter.

Level colors:
  ERROR   → red (bold)
  WARNING → yellow
  INFO    → varies by service (see _SERVICE_COLORS)
  DEBUG   → dark grey

Service colors for INFO:
  src.bot      → cyan
  src.scraper  → green
  src.ai       → magenta
  src.pipeline → blue
  src.database → bright black (grey)
  everything else → white
"""
import logging

# ANSI codes
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

_RED    = "\033[31m"
_YELLOW = "\033[33m"
_BLUE   = "\033[34m"
_MAGENTA= "\033[35m"
_CYAN   = "\033[36m"
_WHITE  = "\033[37m"
_GREEN  = "\033[32m"
_GREY   = "\033[90m"

_LEVEL_COLORS = {
    logging.ERROR:   _BOLD + _RED,
    logging.WARNING: _YELLOW,
    logging.DEBUG:   _GREY,
}

_SERVICE_COLORS = [
    ("src.bot",      _CYAN),
    ("src.scraper",  _GREEN),
    ("src.ai",       _MAGENTA),
    ("src.pipeline", _BLUE),
    ("src.database", _GREY),
]


def _service_color(name: str) -> str:
    for prefix, color in _SERVICE_COLORS:
        if name.startswith(prefix):
            return color
    return _WHITE


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level_color = _LEVEL_COLORS.get(record.levelno)
        if level_color:
            name_color = level_color
        else:
            name_color = _service_color(record.name)

        record.levelname  = f"{level_color or name_color}{record.levelname}{_RESET}"
        record.name       = f"{_BOLD}{name_color}{record.name}{_RESET}"
        record.msg        = f"{level_color or ''}{record.msg}{_RESET if level_color else ''}"

        return super().format(record)


def setup_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("hydrogram").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext.AIORateLimiter").setLevel(logging.CRITICAL)
