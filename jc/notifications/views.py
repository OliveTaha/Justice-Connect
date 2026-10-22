from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST


@login_required
def notification_list(request):
    items = request.user.notifications.select_related("case")[:100]
    return render(request, "notifications/list.html", {"notifications": items})


@login_required
@require_POST
def mark_read(request, pk):
    notification = get_object_or_404(request.user.notifications, pk=pk)
    notification.mark_as_read()
    if notification.case_id:
        return redirect("cases:detail", pk=notification.case_id)
    return redirect("notifications:list")


@login_required
@require_POST
def mark_all_read(request):
    request.user.notifications.filter(is_read=False).update(is_read=True)
    return redirect("notifications:list")
