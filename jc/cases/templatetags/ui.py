from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()


@register.filter
def initials(name):
    parts = str(name or "?").split()
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@register.filter
def first_word(name):
    parts = str(name or "").split()
    return parts[0] if parts else ""


@register.filter
def stars(value):
    """Render a 0-5 rating as five star icons."""
    try:
        filled = int(round(float(value)))
    except (TypeError, ValueError):
        filled = 0
    icons = format_html_join(
        "",
        '<svg class="ic star {}"><use href="#i-star"/></svg>',
        (("on" if i < filled else "off",) for i in range(5)),
    )
    return format_html('<span class="stars">{}</span>', icons)


@register.simple_tag
def avatar(obj, size=""):
    """Round avatar: the profile photo if there is one, otherwise the initials. obj = Account, Lawyer or Client."""
    account = getattr(obj, "account", obj)
    name = getattr(obj, "name", None) or getattr(account, "display_name", "?")
    profile = getattr(account, "profile", None)
    photo = getattr(profile, "photo", None)
    css = f"avatar {size}".strip()
    if photo:
        return format_html('<span class="{}"><img src="{}" alt=""></span>', css, photo.url)
    return format_html('<span class="{}">{}</span>', css, initials(name))
