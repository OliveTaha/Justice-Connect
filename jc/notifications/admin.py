from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("account", "type", "case", "is_read", "created_at")
    list_filter = ("type", "is_read")
