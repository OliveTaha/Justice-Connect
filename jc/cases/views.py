import mimetypes
import os

from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.decorators import role_required
from accounts.models import Account
from notifications.models import Notification

from .forms import (AreaForm, CaseRegisterForm, CaseRequestForm, CourtForm, DeadlineForm, MessageForm,
                    ReviewForm, WithdrawalForm)
from .models import Area, Case, CaseRequest, Court, Deadline, Message, WithdrawalRequest
from .progress import build_progress

STAFF = (Account.COURT_STAFF, Account.ADMIN)


def _deny(request, text="You cannot open that page."):
    messages.error(request, text)
    return redirect("accounts:dashboard")


# ------------------------------------------------------------ court staff
@role_required(*STAFF)
def case_register(request):
    form = CaseRegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            case = d["court"].create_case(
                case_number=d["case_number"], title=d["title"], description=d["description"],
                case_type=d["case_type"], client_name=d["client_name"], client_email=d["client_email"],
                client_phone=d["client_phone"], client_address=d["client_address"],
                notify_client=d["notify_client"],
            )
        except ValueError as exc:
            form.add_error("client_email", str(exc))
        else:
            if case.client_notified:
                messages.success(request, f"Case {case.case_number} registered and the client was notified.")
            else:
                messages.warning(request, f"Case {case.case_number} registered. The client has NOT been notified yet.")
            return redirect("cases:detail", pk=case.pk)
    return render(request, "cases/register.html", {"form": form, "has_courts": Court.objects.exists()})


@role_required(*STAFF)
@require_POST
def notify_client(request, pk):
    case = get_object_or_404(Case, pk=pk)
    case.notify_client()
    messages.success(request, "The client has been notified.")
    return redirect("cases:detail", pk=pk)


@role_required(*STAFF)
def case_manage(request):
    cases = Case.objects.select_related("court", "client", "assigned_lawyer")
    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "")
    court = request.GET.get("court", "")
    if q:
        cases = cases.filter(Q(case_number__icontains=q) | Q(title__icontains=q) | Q(client__name__icontains=q))
    if status:
        cases = cases.filter(status=status)
    if court:
        cases = cases.filter(court_id=court)
    return render(request, "cases/manage.html", {
        "cases": cases, "q": q, "status": status, "court": court,
        "courts": Court.objects.all(), "status_choices": Case.STATUS_CHOICES,
    })


@role_required(*STAFF)
def court_manage(request):
    area_form, court_form = AreaForm(prefix="area"), CourtForm(prefix="court")
    if request.method == "POST":
        if "save_area" in request.POST:
            area_form = AreaForm(request.POST, prefix="area")
            if area_form.is_valid():
                Area.create_area(area_form.cleaned_data["name"])
                messages.success(request, "Area saved.")
                return redirect("cases:courts")
        elif "save_court" in request.POST:
            court_form = CourtForm(request.POST, prefix="court")
            if court_form.is_valid():
                court_form.save()
                messages.success(request, "Court saved.")
                return redirect("cases:courts")
    courts = Court.objects.select_related("area").annotate(case_count=Count("cases"))
    return render(request, "cases/courts.html", {"area_form": area_form, "court_form": court_form, "courts": courts})


# ----------------------------------------------------------------- client
@role_required(Account.CLIENT)
def my_cases(request):
    client = getattr(request.user, "client", None)
    cases = client.view_case() if client else Case.objects.none()
    return render(request, "cases/my_cases.html", {"cases": cases})


@role_required(Account.CLIENT)
@require_POST
def give_consent(request, pk):
    case = get_object_or_404(Case, pk=pk, client__account=request.user, client_notified=True)
    request.user.client.confirm_case(case)
    messages.success(request, "Thank you. Your case is now visible to verified lawyers.")
    return redirect("cases:detail", pk=pk)


def _client_request(request, pk):
    return get_object_or_404(CaseRequest.objects.select_related("case", "lawyer"), pk=pk,
                             case__client__account=request.user)


@role_required(Account.CLIENT)
@require_POST
def select_lawyer(request, pk):
    case_request = _client_request(request, pk)
    try:
        request.user.client.select_lawyer(case_request)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"{case_request.lawyer.name} was notified and must now give the final acceptance.")
    return redirect("cases:detail", pk=case_request.case_id)


@role_required(Account.CLIENT)
@require_POST
def reject_request(request, pk):
    case_request = _client_request(request, pk)
    try:
        request.user.client.reject_request(case_request)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, "Request declined.")
    return redirect("cases:detail", pk=case_request.case_id)


@role_required(Account.CLIENT)
@require_POST
def submit_review(request, pk):
    case = get_object_or_404(Case, pk=pk, client__account=request.user)
    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please choose a rating from 1 to 5 stars.")
    else:
        try:
            request.user.client.review_lawyer(case, form.cleaned_data["rating"], form.cleaned_data["comment"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Thank you for your review!")
    return redirect("cases:detail", pk=pk)


# ----------------------------------------------------------------- lawyer
@role_required(Account.LAWYER)
def available_cases(request):
    lawyer = getattr(request.user, "lawyer", None)
    cases = lawyer.view_cases() if lawyer else Case.objects.none()
    q = request.GET.get("q", "").strip()
    case_type = request.GET.get("type", "")
    if q:
        cases = cases.filter(Q(title__icontains=q) | Q(case_number__icontains=q) | Q(description__icontains=q))
    if case_type:
        cases = cases.filter(case_type=case_type)
    requested_ids = set(lawyer.requests.values_list("case_id", flat=True)) if lawyer else set()
    return render(request, "cases/available.html", {
        "cases": cases, "q": q, "case_type": case_type,
        "type_choices": Case.TYPE_CHOICES, "requested_ids": requested_ids,
    })


@role_required(Account.LAWYER)
def my_requests(request):
    lawyer = getattr(request.user, "lawyer", None)
    reqs = lawyer.requests.select_related("case", "case__court") if lawyer else CaseRequest.objects.none()
    return render(request, "cases/my_requests.html", {"requests": reqs})


@role_required(Account.LAWYER)
@require_POST
def request_case(request, pk):
    case = get_object_or_404(Case, pk=pk)
    form = CaseRequestForm(request.POST)
    if form.is_valid():
        try:
            request.user.lawyer.request_case(case, form.cleaned_data["message"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Your request was sent. You can now chat with the client in safe mode.")
    else:
        messages.error(request, "Please write a short message for the client.")
    return redirect("cases:detail", pk=pk)


def _lawyer_request(request, pk):
    return get_object_or_404(CaseRequest.objects.select_related("case", "lawyer"), pk=pk,
                             lawyer__account=request.user)


@role_required(Account.LAWYER)
@require_POST
def lawyer_accept(request, pk):
    case_request = _lawyer_request(request, pk)
    try:
        request.user.lawyer.accept_request(case_request)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Final acceptance done. You are now the lawyer for this case.")
    return redirect("cases:detail", pk=case_request.case_id)


@role_required(Account.LAWYER)
@require_POST
def lawyer_decline(request, pk):
    case_request = _lawyer_request(request, pk)
    try:
        request.user.lawyer.decline_request(case_request)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.info(request, "You declined this case.")
    return redirect("cases:my_requests")


# ---------------------------------------------------------- chat (safe mode)
def _chat_context(request, pk):
    case_request = get_object_or_404(
        CaseRequest.objects.select_related("case", "case__client__account", "lawyer__account"), pk=pk)
    user = request.user
    is_client = user.role == Account.CLIENT and case_request.case.client.account_id == user.id
    is_lawyer = user.role == Account.LAWYER and case_request.lawyer.account_id == user.id
    return case_request, is_client, is_lawyer


def _mark_chat_read(user, case):
    user.notifications.filter(case=case, type=Notification.NEW_MESSAGE, is_read=False).update(is_read=True)


@role_required(Account.CLIENT, Account.LAWYER)
def request_chat(request, pk):
    case_request, is_client, is_lawyer = _chat_context(request, pk)
    user = request.user
    ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if not (is_client or is_lawyer):
        if ajax:
            return JsonResponse({"ok": False, "error": "You are not part of this conversation."}, status=403)
        return _deny(request, "You are not part of this conversation.")

    form = MessageForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        error = None
        if form.is_valid():
            try:
                message = case_request.send_message(user, form.cleaned_data["body"], form.cleaned_data.get("attachment"))
            except ValueError as exc:
                error = str(exc)
            else:
                if ajax:
                    return JsonResponse({"ok": True, "message": message.as_dict(user)})
                return redirect("cases:chat", pk=pk)
        else:
            error = "Please write a message or attach a file."
        if ajax:
            return JsonResponse({"ok": False, "error": error}, status=400)
        messages.error(request, error)
        return redirect("cases:chat", pk=pk)

    _mark_chat_read(user, case_request.case)

    if is_client:
        other_label = case_request.lawyer.name
    elif case_request.status in (CaseRequest.SELECTED, CaseRequest.ACCEPTED):
        other_label = case_request.case.client.name
    else:
        other_label = "Client"  # identity stays private until the client selects the lawyer

    thread = list(case_request.messages.select_related("sender"))
    return render(request, "cases/chat.html", {
        "req": case_request, "case": case_request.case, "thread": [m.as_dict(user) for m in thread],
        "last_id": thread[-1].pk if thread else 0,
        "form": form, "is_client": is_client, "is_lawyer": is_lawyer, "other_label": other_label,
        "can_send": case_request.status not in (CaseRequest.REJECTED, CaseRequest.WITHDRAWN),
        "max_mb": 5,
    })


@role_required(Account.CLIENT, Account.LAWYER)
def chat_poll(request, pk):
    """New messages since ?after=<id> (used by the live chat)."""
    case_request, is_client, is_lawyer = _chat_context(request, pk)
    if not (is_client or is_lawyer):
        return JsonResponse({"ok": False}, status=403)
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    new = list(case_request.messages.filter(pk__gt=after))
    if new:
        _mark_chat_read(request.user, case_request.case)
    return JsonResponse({"ok": True, "status": case_request.status,
                         "messages": [m.as_dict(request.user) for m in new]})


@role_required(Account.CLIENT, Account.LAWYER)
def message_file(request, pk):
    """Chat attachments are only given to the two people in the conversation."""
    message = get_object_or_404(Message.objects.select_related("request__case__client__account", "request__lawyer__account"), pk=pk)
    req = message.request
    if request.user.id not in (req.case.client.account_id, req.lawyer.account_id) or not message.attachment:
        raise Http404()
    name = message.attachment_name or os.path.basename(message.attachment.name)
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    inline = message.is_image or content_type == "application/pdf"
    response = FileResponse(message.attachment.open("rb"), content_type=content_type)
    disposition = "inline" if inline else "attachment"
    response["Content-Disposition"] = f'{disposition}; filename="{name}"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


# ------------------------------------------- withdrawal of a running case
@role_required(Account.LAWYER)
@require_POST
def request_withdrawal(request, pk):
    case = get_object_or_404(Case, pk=pk, assigned_lawyer__account=request.user)
    form = WithdrawalForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Please explain the reason (at least 10 characters).")
    else:
        try:
            request.user.lawyer.request_withdrawal(case, form.cleaned_data["reason"])
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Your withdrawal request was sent to the client.")
    return redirect("cases:detail", pk=pk)


@role_required(Account.CLIENT)
@require_POST
def respond_withdrawal(request, pk):
    withdrawal = get_object_or_404(WithdrawalRequest.objects.select_related("case", "lawyer"), pk=pk,
                                   case__client__account=request.user)
    approve = request.POST.get("decision") == "approve"
    try:
        request.user.client.respond_withdrawal(withdrawal, approve)
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "The lawyer has left the case. It is open for new lawyers again." if approve
                         else "You declined the request. The lawyer stays on your case.")
    return redirect("cases:detail", pk=withdrawal.case_id)


# ------------------------------------------------------------ deadlines
@role_required(*STAFF)
@require_POST
def add_deadline(request, pk):
    case = get_object_or_404(Case, pk=pk)
    form = DeadlineForm(request.POST)
    if form.is_valid():
        d = form.cleaned_data
        Deadline.add(case, d["title"], d["due_at"], d["note"], by=request.user)
        messages.success(request, "Deadline added. The client and lawyer can see it.")
    else:
        messages.error(request, " ".join(e for errs in form.errors.values() for e in errs) or "Invalid deadline.")
    return redirect("cases:detail", pk=pk)


@role_required(*STAFF)
@require_POST
def delete_deadline(request, pk):
    deadline = get_object_or_404(Deadline, pk=pk)
    case_pk = deadline.case_id
    deadline.delete()
    messages.info(request, "Deadline removed.")
    return redirect("cases:detail", pk=case_pk)


# ----------------------------------------------------------- close / shared
@role_required(Account.LAWYER, *STAFF)
@require_POST
def close_case(request, pk):
    case = get_object_or_404(Case, pk=pk)
    try:
        if request.user.role == Account.LAWYER:
            request.user.lawyer.close_case(case)
        else:
            case.close_case()
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Case marked as solved. The client can now rate the lawyer.")
    return redirect("cases:detail", pk=pk)


def _can_view(user, case):
    if user.role in STAFF:
        return True
    if user.role == Account.CLIENT:
        return case.client.account_id == user.id and case.client_notified
    if user.role == Account.LAWYER:
        lawyer = getattr(user, "lawyer", None)
        if lawyer is None:
            return False
        if case.assigned_lawyer_id == lawyer.id or case.requests.filter(lawyer=lawyer).exists():
            return True
        return case.consent and case.client_notified and case.assigned_lawyer_id is None and user.is_verified
    return False


@role_required(Account.CLIENT, Account.LAWYER, *STAFF)
def case_detail(request, pk):
    case = get_object_or_404(
        Case.objects.select_related("court", "court__area", "client__account", "assigned_lawyer"), pk=pk)
    user = request.user
    if not _can_view(user, case):
        return _deny(request, "You cannot view this case.")

    review = getattr(case, "review", None)
    ctx = {"case": case, "progress": build_progress(case, user), "is_owner": False, "is_assigned_to_me": False,
           "my_request": None, "can_request": False, "case_requests": None, "review": review,
           "can_review": False, "can_close": False}

    if user.role == Account.CLIENT:
        ctx["is_owner"] = True
        ctx["case_requests"] = case.requests.select_related("lawyer__account__profile")
        ctx["can_review"] = case.status == Case.CLOSED and review is None
        ctx["pending_withdrawal"] = case.withdrawals.filter(status=WithdrawalRequest.PENDING).first()
    elif user.role == Account.LAWYER:
        lawyer = user.lawyer
        ctx["is_assigned_to_me"] = case.assigned_lawyer_id == lawyer.id
        ctx["my_request"] = case.requests.filter(lawyer=lawyer).first()
        ctx["can_request"] = (case.consent and case.assigned_lawyer_id is None
                              and ctx["my_request"] is None and user.is_verified)
        ctx["request_form"] = CaseRequestForm()
        ctx["can_close"] = ctx["is_assigned_to_me"] and case.status == Case.ASSIGNED
        ctx["pending_withdrawal"] = case.withdrawals.filter(status=WithdrawalRequest.PENDING, lawyer=lawyer).first()
        ctx["withdrawal_form"] = WithdrawalForm()
    else:
        ctx["case_requests"] = case.requests.select_related("lawyer__account__profile")
        ctx["can_close"] = case.status == Case.ASSIGNED
        ctx["pending_withdrawal"] = case.withdrawals.filter(status=WithdrawalRequest.PENDING).first()
        ctx["deadline_form"] = DeadlineForm()

    ctx["deadlines"] = case.deadlines.all()
    ctx["client_history"] = {"total": case.client.total_cases, "solved": case.client.solved_cases}
    ctx["show_client_info"] = user.role in STAFF or ctx["is_owner"] or ctx["is_assigned_to_me"]
    return render(request, "cases/detail.html", ctx)
