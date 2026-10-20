"""Django settings for the Justice Connect university project."""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-justice-connect-change-me"
DEBUG = True
ALLOWED_HOSTS = ["*"]  # development: lets you open the site from a phone on the same WiFi

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Justice Connect apps
    "accounts",
    "cases",
    "notifications",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "justiceconnect.middleware.DeadlineMiddleware",
    "justiceconnect.middleware.LanguageMiddleware",  # keep last: it post-processes the rendered page
]

ROOT_URLCONF = "justiceconnect.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "notifications.context_processors.notification_count",
                "justiceconnect.context.ui",
            ],
        },
    },
]

WSGI_APPLICATION = "justiceconnect.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# Custom user model (class "Account" in the UML diagram)
AUTH_USER_MODEL = "accounts.Account"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "accounts:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Dhaka"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"          # profile photos (public)
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"  # chat files (only participants can open them)
CHAT_MAX_UPLOAD_MB = 5
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024

# A lawyer asks to leave a running case. If the client does not answer within this time, it happens automatically.
WITHDRAWAL_AUTO_HOURS = 72

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Used to build the (simulated) verification links sent to users
SITE_URL = "http://127.0.0.1:8000"
