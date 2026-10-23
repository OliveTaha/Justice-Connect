from django.contrib import messages
from django.contrib.auth import authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from cases.models import Case, CaseRequest, Deadline, WithdrawalRequest
from notifications.models import Notification

from .forms import DeleteAccountForm, LawyerRegisterForm, LoginForm, ProfileForm, VerifyPasswordForm
from .models import Account, Client, Lawyer, Profile


# ------------------------------------------------------------------ auth
def login_view(request):
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip()
        account = Account.objects.filter(email__iexact=email).first()
        user = authenticate(request, username=account.email, password=form.cleaned_data["password"]) if account else None

        if user is None:
            form.add_error(None, "Invalid email or password.")
        elif user.role in (Account.CLIENT, Account.LAWYER) and not user.is_verified:
            form.add_error(None, "Your account is not verified yet. Please use the verification link you received.")
        else:
            user.login(request)
            if not form.cleaned_data["remember"]:
                request.session.set_expiry(0)  # session ends when the browser closes
            next_url = request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("accounts:dashboard")

    return render(request, "accounts/login.html", {"form": form})


@require_POST
def logout_view(request):
    Account.logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("accounts:login")


def register_view(request):
    """Sign-up for lawyers. (Clients are created automatically by courts.)"""
    if request.user.is_authenticated:
        return redirect("accounts:dashboard")

    form = LawyerRegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            account = Account.register(email=d["email"], password=d["password"], role=Account.LAWYER)
            Lawyer.objects.create(account=account, name=d["name"], bar_id=d["bar_id"])
            Profile.objects.create(account=account, name=d["name"])
            Notification.send(
                account,
                f"Welcome to Justice Connect! Please verify your account: {account.verification_url}",
                Notification.ACCOUNT_VERIFICATION,
            )
        return redirect("accounts:register_done", token=account.verification_token)

    return render(request, "accounts/register.html", {"form": form})


def register_done_view(request, token):
    account = get_object_or_404(Account, verification_token=token)
    return render(request, "accounts/register_done.html", {"account": account})


def _after_verification(account):
    """Once a client is verified, ask for consent on every case waiting for it."""
    client = getattr(account, "client", None)
    if client is None:
        return
    for case in client.cases.filter(consent=False, client_notified=True):
        Notification.send(
            account,
            f"Please review case {case.case_number} and give your consent so lawyers can see it.",
            Notification.CONSENT_REQUEST,
            case,
        )


def verify_view(request, token):
    account = get_object_or_404(Account, verification_token=token)
    needs_password = not account.has_usable_password()

    if account.is_verified and not needs_password:
        messages.info(request, "Your account is already verified. Please sign in.")
        return redirect("accounts:login")

    form = VerifyPasswordForm(request.POST or None) if needs_password else None
    if request.method == "POST" and (form is None or form.is_valid()):
        if form is not None:
            account.set_password(form.cleaned_data["password"])
            account.save(update_fields=["password"])
        account.verify_account()
        _after_verification(account)
        account.login(request)
        messages.success(request, "Your account has been verified. Welcome to Justice Connect!")
        return redirect("accounts:dashboard")

    return render(request, "accounts/verify.html", {"account": account, "form": form, "needs_password": needs_password})


# ------------------------------------------------------------- dashboard
@login_required
def dashboard_view(request):
    user = request.user
    now = timezone.now()
    ctx = {"stats": [], "show_guide": not user.guide_seen and user.role != Account.ADMIN}

    if user.role == Account.CLIENT:
        client = getattr(user, "client", None)
        cases = client.view_case() if client else Case.objects.none()
        ctx["stats"] = [
            ("My Cases", cases.count(), "folder"),
            ("Awaiting My Consent", cases.filter(consent=False).count(), "clock"),
            ("Lawyer Requests", CaseRequest.objects.filter(case__client=client, status__in=[CaseRequest.PENDING, CaseRequest.SELECTED]).count() if client else 0, "send"),
            ("Solved Cases", cases.filter(status=Case.CLOSED).count(), "award"),
        ]
        ctx["recent_cases"] = cases[:5]
        ctx["deadlines"] = Deadline.objects.filter(case__in=cases).exclude(case__status=Case.CLOSED).select_related("case")[:5]
        ctx["pending_withdrawals"] = WithdrawalRequest.objects.filter(
            case__in=cases, status=WithdrawalRequest.PENDING).select_related("case", "lawyer")
        total, solved = (client.total_cases, client.solved_cases) if client else (0, 0)
        ctx["history"] = {"total": total, "solved": solved, "active": total - solved}

    elif user.role == Account.LAWYER:
        lawyer = getattr(user, "lawyer", None)
        available = lawyer.view_cases() if lawyer else Case.objects.none()
        my_requests = lawyer.requests.select_related("case") if lawyer else CaseRequest.objects.none()
        rating = lawyer.average_rating if lawyer else None
        ctx["stats"] = [
            ("Available Cases", available.count(), "folder"),
            ("Pending Requests", my_requests.filter(status=CaseRequest.PENDING).count(), "send"),
            ("Need My Final Decision", my_requests.filter(status=CaseRequest.SELECTED).count(), "clock"),
            ("Cases Solved", lawyer.cases_solved if lawyer else 0, "award"),
            ("Rating", f"{rating} / 5" if rating else "No reviews yet", "star"),
        ]
        ctx["recent_cases"] = available[:5]
        ctx["recent_requests"] = my_requests[:5]
        ctx["lawyer"] = lawyer
        ctx["deadlines"] = Deadline.objects.filter(case__assigned_lawyer=lawyer).exclude(case__status=Case.CLOSED).select_related("case")[:5] if lawyer else []

    elif user.role == Account.COURT_STAFF:
        cases = Case.objects.select_related("court", "client")
        ctx["stats"] = [
            ("Total Cases", cases.count(), "folder"),
            ("Awaiting Consent", cases.filter(consent=False).count(), "clock"),
            ("Open for Lawyers", cases.filter(status=Case.OPEN).count(), "search"),
            ("Lawyer Assigned", cases.filter(status=Case.ASSIGNED).count(), "user"),
            ("Solved", cases.filter(status=Case.CLOSED).count(), "award"),
        ]
        ctx["recent_cases"] = cases[:5]
        ctx["deadlines"] = Deadline.objects.filter(due_at__gte=now - timezone.timedelta(days=1)).exclude(case__status=Case.CLOSED).select_related("case")[:5]

    else:  # admin
        ctx["stats"] = [
            ("Total Accounts", Account.objects.count(), "user"),
            ("Clients", Account.objects.filter(role=Account.CLIENT).count(), "user"),
            ("Lawyers", Account.objects.filter(role=Account.LAWYER).count(), "briefcase"),
            ("Unverified Accounts", Account.objects.filter(is_verified=False).count(), "clock"),
            ("Total Cases", Case.objects.count(), "folder"),
            ("Case Requests", CaseRequest.objects.count(), "send"),
        ]
        ctx["recent_cases"] = Case.objects.select_related("court", "client")[:5]

    return render(request, "accounts/dashboard.html", ctx)


@login_required
@require_POST
def guide_done(request):
    """User manual closed. 'Don't show again' stops it from appearing at the next login."""
    if request.POST.get("dont_show") == "1":
        request.user.guide_seen = True
        request.user.save(update_fields=["guide_seen"])
    return JsonResponse({"ok": True})


def set_language(request, code):
    """Remember the language in a cookie (a cookie survives log in / log out, a session would not)."""
    code = "bn" if code == "bn" else "en"
    target = request.META.get("HTTP_REFERER", "")
    if not target or not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = "/dashboard/" if request.user.is_authenticated else "/"
    response = redirect(target)
    response.set_cookie("jc_lang", code, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


# ---------------------------------------------------- account settings
@login_required
def settings_view(request):
    user = request.user
    profile, _ = Profile.objects.get_or_create(account=user, defaults={"name": user.display_name})
    profile_form = ProfileForm(instance=profile, role=user.role)
    password_form = PasswordChangeForm(user)
    delete_form = DeleteAccountForm(user)
    tab = request.GET.get("tab", "profile")
    client = getattr(user, "client", None)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "profile":
            tab = "profile"
            profile_form = ProfileForm(request.POST, request.FILES, instance=profile, role=user.role)
            if profile_form.is_valid():
                profile = profile_form.save()
                for rel in ("client", "lawyer"):  # keep the role-specific name in sync
                    obj = getattr(user, rel, None)
                    if obj is not None:
                        obj.name = profile.name
                        obj.save(update_fields=["name"])
                messages.success(request, "Profile updated.")
                return redirect("accounts:settings")
        elif action == "password":
            tab = "security"
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed.")
                return redirect("/settings/?tab=security")
        elif action == "delete" and user.role == Account.CLIENT:
            tab = "danger"
            delete_form = DeleteAccountForm(user, request.POST)
            if client and client.unsolved_visible_cases:
                messages.error(request, "You can delete your account only after all your cases are solved.")
            elif delete_form.is_valid():
                user.delete_account()
                Account.logout(request)
                messages.success(request, "Your account was deleted. Your past case record stays in the court system.")
                return redirect("accounts:login")

    return render(request, "accounts/settings.html", {
        "profile": profile, "profile_form": profile_form, "password_form": password_form,
        "delete_form": delete_form, "tab": tab,
        "can_delete": user.role == Account.CLIENT and not (client and client.unsolved_visible_cases),
        "client": client,
    })


# ------------------------------------------------------- lawyer directory
def _ranked_lawyers(query="", sort="rating"):
    lawyers = Lawyer.objects.select_related("account", "account__profile").filter(account__is_verified=True, account__is_active=True)
    rows = []
    for lawyer in lawyers:
        profile = getattr(lawyer.account, "profile", None)
        if query and query.lower() not in f"{lawyer.name} {getattr(profile, 'specialization', '')} {getattr(profile, 'location', '')}".lower():
            continue
        rows.append({"lawyer": lawyer, "profile": profile, "rating": lawyer.average_rating,
                     "reviews": lawyer.review_count, "solved": lawyer.cases_solved,
                     "experience": getattr(profile, "experience", 0) or 0})
    if sort == "solved":
        rows.sort(key=lambda r: (r["solved"], r["rating"] or 0), reverse=True)
    elif sort == "experience":
        rows.sort(key=lambda r: (r["experience"], r["rating"] or 0), reverse=True)
    else:
        rows.sort(key=lambda r: (r["rating"] or 0, r["reviews"], r["solved"]), reverse=True)
    return rows


@login_required
def lawyers_list(request):
    query = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "rating")
    return render(request, "accounts/lawyers.html", {"rows": _ranked_lawyers(query, sort), "q": query, "sort": sort})


@login_required
def lawyer_detail(request, pk):
    lawyer = get_object_or_404(Lawyer.objects.select_related("account"), pk=pk)
    user = request.user
    if user.role == Account.LAWYER and lawyer.account_id != user.id:
        messages.error(request, "You can only view your own lawyer profile.")
        return redirect("accounts:dashboard")
    profile = getattr(lawyer.account, "profile", None)
    reviews = lawyer.reviews.select_related("client", "case")
    return render(request, "accounts/lawyer_detail.html", {"lawyer": lawyer, "profile": profile, "reviews": reviews})
