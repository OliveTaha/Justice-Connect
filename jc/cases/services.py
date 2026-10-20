"""Time based jobs. They run lazily (see justiceconnect/middleware.py) or via `manage.py process_due`."""
from datetime import timedelta

from django.utils import timezone


def process_due():
    from .models import Deadline, WithdrawalRequest

    now = timezone.now()
    done = 0
    # 1) lawyer asked to withdraw and the client did not answer in time -> automatic withdrawal
    for item in WithdrawalRequest.objects.filter(status=WithdrawalRequest.PENDING, auto_at__lte=now):
        item.approve(auto=True)
        done += 1
    # 2) deadline reminders (24 hours before)
    soon = now + timedelta(hours=24)
    for item in Deadline.objects.filter(reminded=False, due_at__gt=now, due_at__lte=soon).select_related("case"):
        item.remind()
        done += 1
    return done
