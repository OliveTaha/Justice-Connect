import logging
import time

from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)


class DeadlineMiddleware:
    """Runs the time based jobs (automatic withdrawal, deadline reminders) at most every 30 seconds."""

    interval = 30
    _last = 0.0

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        now = time.monotonic()
        if now - DeadlineMiddleware._last > self.interval:
            DeadlineMiddleware._last = now
            try:
                from cases.services import process_due

                process_due()
            except Exception:  # e.g. database not migrated yet
                logger.exception("process_due failed")
        return self.get_response(request)


class LanguageMiddleware:
    """English / Bangla switch. Bangla pages are translated after rendering (see translations.py)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .translations import translate_html

        lang = "bn" if request.COOKIES.get("jc_lang") == "bn" else "en"
        request.lang = lang
        response = self.get_response(request)
        if lang == "bn" and not response.streaming and "text/html" in response.get("Content-Type", ""):
            charset = response.charset or "utf-8"
            response.content = translate_html(response.content.decode(charset)).encode(charset)
            if response.has_header("Content-Length"):
                response["Content-Length"] = str(len(response.content))
        return response
