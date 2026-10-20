from django.contrib import admin

from .models import Area, Case, CaseRequest, Court, Deadline, Message, Review, WithdrawalRequest


@admin.register(Area)
class AreaAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ("name", "area", "address")
    list_filter = ("area",)


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("case_number", "title", "court", "client", "status", "consent", "client_notified", "assigned_lawyer")
    list_filter = ("status", "consent", "client_notified", "case_type", "court")
    search_fields = ("case_number", "title", "client__name")


@admin.register(CaseRequest)
class CaseRequestAdmin(admin.ModelAdmin):
    list_display = ("case", "lawyer", "status", "requested_at")
    list_filter = ("status",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("request", "sender", "created_at")


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("case", "lawyer", "rating", "created_at")
    list_filter = ("rating",)


@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ("case", "lawyer", "status", "created_at", "auto_at")
    list_filter = ("status",)


@admin.register(Deadline)
class DeadlineAdmin(admin.ModelAdmin):
    list_display = ("case", "title", "due_at", "reminded")
