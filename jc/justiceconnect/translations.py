"""Bangla translation of rendered pages.

How it works: when the visitor chose Bangla, every text node (and a few attributes such as title/placeholder)
of the rendered HTML is looked up in `bn_dictionary.EXACT` (whole sentence match) or `bn_dictionary.PATTERNS`
(sentences that contain names/numbers). To translate something new just add one line to bn_dictionary.py.
"""
import html
import re

from .bn_dictionary import EXACT, PATTERNS

TOKEN_RE = re.compile(r"(<!--.*?-->|<style\b.*?</style>|<script\b.*?</script>|<[^>]+>)", re.S | re.I)
ATTR_RE = re.compile(
    r"""\b(title|placeholder|aria-label|data-confirm|data-confirm-title|data-confirm-btn|data-next|data-done|data-big|alt)=(?:"([^"]*)"|'([^']*)')""")
_COMPILED = [(re.compile(pattern, re.S), template) for pattern, template in PATTERNS]


def lookup(text):
    """Return the Bangla text for `text` (already unescaped and whitespace-normalised) or None."""
    found = EXACT.get(text)
    if found is not None:
        return found
    for pattern, template in _COMPILED:
        match = pattern.fullmatch(text)
        if match:
            parts = {k: EXACT.get(v, v) for k, v in match.groupdict().items()}
            return template.format(**parts)
    return None


def _text_node(text):
    stripped = text.strip()
    if not stripped:
        return text
    normalised = re.sub(r"\s+", " ", html.unescape(stripped))
    result = lookup(normalised)
    if result is None:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    return lead + html.escape(result, quote=False) + trail


def _tag(tag):
    if "=" not in tag:
        return tag

    def repl(match):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        result = lookup(re.sub(r"\s+", " ", html.unescape(value).strip()))
        if result is None:
            return match.group(0)
        return f'{match.group(1)}="{html.escape(result, quote=True)}"'

    return ATTR_RE.sub(repl, tag)


def translate_html(page):
    out = []
    for token in TOKEN_RE.split(page):
        if not token:
            continue
        low = token[:7].lower()
        if token.startswith("<!--") or low.startswith("<style") or low.startswith("<script"):
            out.append(token)
        elif token.startswith("<"):
            out.append(_tag(token))
        else:
            out.append(_text_node(token))
    result = "".join(out)
    return result.replace('<html lang="en"', '<html lang="bn"', 1)


def tr(text, lang="en"):
    """Translate one string in Python code (for flash messages etc.)."""
    if lang != "bn":
        return text
    return lookup(re.sub(r"\s+", " ", text.strip())) or text
