"""cases app: Area, Court, Case, CaseRequest (+ Message, Review added later)."""
import os
import re
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d")
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}|\d{2}[-/.]\d{2}[-/.]\d{4})")


ALLOWED_ATTACHMENTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".pdf", ".doc", ".docx", ".txt"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


def private_storage():
    """Chat files are NOT served from a public URL; they go through a permission-checked view."""
    from django.core.files.storage import FileSystemStorage

    return FileSystemStorage(location=settings.PRIVATE_MEDIA_ROOT)


def validate_attachment(upload):
    ext = os.path.splitext(upload.name)[1].lower()
    if ext not in ALLOWED_ATTACHMENTS:
        raise ValueError("This file type is not allowed. Use an image, PDF, Word or text file.")
    limit_mb = getattr(settings, "CHAT_MAX_UPLOAD_MB", 5)
    if upload.size > limit_mb * 1024 * 1024:
        raise ValueError(f"The file is too large (maximum {limit_mb} MB).")


def hide_contact_details(text):
    """Safe mode: hide e-mail addresses and phone numbers in chat messages."""
    text = EMAIL_RE.sub("[hidden]", text)

    def _mask(match):
        found = match.group(0)
        digits = sum(ch.isdigit() for ch in found)
        if digits >= 8 and not DATE_RE.match(found):  # dates are not phone numbers
            return "[hidden]"
        return found

    return PHONE_RE.sub(_mask, text)


class Area(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @classmethod
    def create_area(cls, name):
        area, _ = cls.objects.get_or_create(name=name.strip())
        return area

    def update_area(self, name):
        self.name = name.strip()
        self.save(update_fields=["name"])
        return self


class Court(models.Model):
    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255)
    area = models.ForeignKey(Area, on_delete=models.PROTECT, related_name="courts")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.area})"

    def create_case(self, **kwargs):
        return Case.create_case(court=self, **kwargs)

    def manage_cases(self):
        return self.cases.select_related("client", "assigned_lawyer")


class CaseQuerySet(models.QuerySet):
    def available(self):
        """Cases the client consented to and that still have no lawyer."""
        return self.filter(consent=True, assigned_lawyer__isnull=True, client_notified=True)


class Case(models.Model):
    REGISTERED = "registered"
    OPEN = "open"
    ASSIGNED = "assigned"
    CLOSED = "closed"
    STATUS_CHOICES = [
        (REGISTERED, "Registered"),
        (OPEN, "Open for Lawyers"),
        (ASSIGNED, "Lawyer Assigned"),
        (CLOSED, "Solved"),
    ]
    TYPE_CHOICES = [(t, t) for t in ("Civil", "Criminal", "Family", "Property", "Labour", "Other")]

    case_number = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField()
    case_type = models.CharField(max_length=30, choices=TYPE_CHOICES, default="Other")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=REGISTERED)
    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name="cases")
    client = models.ForeignKey("accounts.Client", on_delete=models.CASCADE, related_name="cases")
    assigned_lawyer = models.ForeignKey(
        "accounts.Lawyer", on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_cases"
    )
    consent = models.BooleanField(default=False)
    consent_date = models.DateTimeField(null=True, blank=True)
    # The court decides whether (and when) the client is told about the case.
    client_notified = models.BooleanField(default=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CaseQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.case_number} - {self.title}"

    # ---- UML methods ----
    @classmethod
    @transaction.atomic
    def create_case(cls, court, case_number, title, description, case_type,
                    client_name, client_email, client_phone="", client_address="",
                    notify_client=True):
        """Court registers a case; the client account is created/linked automatically."""
        from accounts.models import Account, Client

        account = Account.for_client_email(client_email)  # creates, or re-opens a deleted account

        client, _ = Client.objects.get_or_create(
            account=account,
            defaults={"name": client_name, "phone": client_phone, "address": client_address},
        )
        case = cls.objects.create(
            court=court, client=client, case_number=case_number, title=title,
            description=description, case_type=case_type, client_notified=False,
        )
        if notify_client:
            case.notify_client()
        return case

    def notify_client(self):
        """Tell the client about the case (verification link or consent request)."""
        from notifications.models import Notification

        if self.client_notified and self.notifications.exists():
            return self
        account = self.client.account
        if account.is_verified:
            Notification.send(
                account,
                f"A new case ({self.case_number}) was registered for you. Please review it and give your consent.",
                Notification.CONSENT_REQUEST, self,
            )
        else:
            Notification.send(
                account,
                f"A case ({self.case_number}) was registered for you at {self.court.name}. "
                f"Verify your account to view it: {account.verification_url}",
                Notification.ACCOUNT_VERIFICATION, self,
            )
        self.client_notified = True
        self.save(update_fields=["client_notified"])
        return self

    def view_case(self):
        return self

    def confirm_case(self):
        """Client consent: case becomes available to eligible (verified) lawyers."""
        from accounts.models import Account
        from notifications.models import Notification

        if self.consent:
            return self
        self.consent = True
        self.consent_date = timezone.now()
        if self.assigned_lawyer_id is None:
            self.status = self.OPEN
        self.save(update_fields=["consent", "consent_date", "status"])

        Notification.send(
            self.client.account,
            f"Your consent for case {self.case_number} was recorded. Lawyers can now request it.",
            Notification.CASE_UPDATE, self,
        )
        for lawyer_account in Account.objects.filter(role=Account.LAWYER, is_verified=True):
            Notification.send(
                lawyer_account,
                f"New case available: {self.title} ({self.case_number}).",
                Notification.NEW_CASE, self,
            )
        return self

    def assign_lawyer(self, lawyer):
        from notifications.models import Notification

        self.assigned_lawyer = lawyer
        self.status = self.ASSIGNED
        self.assigned_at = timezone.now()
        self.save(update_fields=["assigned_lawyer", "status", "assigned_at"])
        Notification.send(
            lawyer.account,
            f"Final acceptance done. You are now the lawyer for case {self.case_number}.",
            Notification.LAWYER_ASSIGNED, self,
        )
        Notification.send(
            self.client.account,
            f"{lawyer.name} accepted your case {self.case_number}. You can now share contact details in the chat.",
            Notification.LAWYER_ASSIGNED, self,
        )
        return self

    def release_lawyer(self):
        """The assigned lawyer leaves the case (withdrawal). The case is open for new requests again."""
        from accounts.models import Account
        from notifications.models import Notification

        lawyer = self.assigned_lawyer
        self.assigned_lawyer = None
        self.assigned_at = None
        self.status = self.OPEN if self.consent else self.REGISTERED
        self.save(update_fields=["assigned_lawyer", "assigned_at", "status"])
        self.requests.filter(lawyer=lawyer, status=CaseRequest.ACCEPTED).update(status=CaseRequest.WITHDRAWN)
        if self.consent:
            for acct in Account.objects.filter(role=Account.LAWYER, is_verified=True).exclude(pk=lawyer.account_id):
                Notification.send(
                    acct, f"A case is open again: {self.title} ({self.case_number}).",
                    Notification.NEW_CASE, self,
                )
        return lawyer

    def close_case(self):
        """Mark the case as solved (so the client can rate the lawyer)."""
        from notifications.models import Notification

        if self.status != self.ASSIGNED:
            raise ValueError("Only a case with an assigned lawyer can be marked as solved.")
        self.status = self.CLOSED
        self.closed_at = timezone.now()
        self.save(update_fields=["status", "closed_at"])
        Notification.send(
            self.client.account,
            f"Case {self.case_number} was marked as solved. Please rate your lawyer.",
            Notification.CASE_UPDATE, self,
        )
        return self


class CaseRequest(models.Model):
    PENDING = "pending"
    SELECTED = "selected"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (SELECTED, "Awaiting lawyer's final acceptance"),
        (ACCEPTED, "Accepted"),
        (REJECTED, "Rejected"),
        (WITHDRAWN, "Withdrawn"),
    ]

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="requests")
    lawyer = models.ForeignKey("accounts.Lawyer", on_delete=models.CASCADE, related_name="requests")
    message = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    requested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-requested_at"]
        unique_together = [("case", "lawyer")]

    def __str__(self):
        return f"{self.lawyer.name} -> {self.case.case_number} ({self.status})"

    @property
    def is_open(self):
        return self.status in (self.PENDING, self.SELECTED)

    @property
    def selectable(self):
        """Can the client select this lawyer right now?"""
        return (
            self.status == self.PENDING
            and self.case.assigned_lawyer_id is None
            and not self.case.requests.filter(status=self.SELECTED).exists()
        )

    # ---- UML methods ----
    @classmethod
    def submit_request(cls, case, lawyer, message=""):
        from notifications.models import Notification

        if not lawyer.account.is_verified:
            raise ValueError("Your account must be verified before requesting cases.")
        if not case.consent or case.assigned_lawyer_id is not None:
            raise ValueError("This case is not available for requests.")
        if cls.objects.filter(case=case, lawyer=lawyer).exists():
            raise ValueError("You have already sent a request for this case.")

        req = cls.objects.create(case=case, lawyer=lawyer, message=message)
        Notification.send(
            case.client.account,
            f"{lawyer.name} requested to represent your case {case.case_number}. You can chat with the lawyer before deciding.",
            Notification.LAWYER_REQUEST, case,
        )
        return req

    def select(self):
        """Step 1 (client): choose this lawyer. The lawyer must still give final acceptance."""
        from notifications.models import Notification

        if not self.selectable:
            raise ValueError("This lawyer cannot be selected right now.")
        self.status = self.SELECTED
        self.save(update_fields=["status"])
        Notification.send(
            self.lawyer.account,
            f"The client selected you for case {self.case.case_number}. Review the chat and give your final decision.",
            Notification.CASE_UPDATE, self.case,
        )
        return self

    @transaction.atomic
    def accept(self):
        """Step 2 (lawyer): final acceptance -> Case.assignedLawyer is set."""
        from notifications.models import Notification

        if self.status != self.SELECTED or self.case.assigned_lawyer_id is not None:
            raise ValueError("The client has not selected you for this case (or it is already assigned).")
        self.status = self.ACCEPTED
        self.save(update_fields=["status"])
        self.case.assign_lawyer(self.lawyer)

        for other in self.case.requests.exclude(pk=self.pk).filter(status__in=[self.PENDING, self.SELECTED]):
            other.status = self.REJECTED
            other.save(update_fields=["status"])
            Notification.send(
                other.lawyer.account,
                f"Case {self.case.case_number} was assigned to another lawyer.",
                Notification.CASE_UPDATE, self.case,
            )
        return self

    def reject(self, by="client"):
        """Client declines the lawyer, or the lawyer declines/withdraws before final acceptance."""
        from notifications.models import Notification

        if not self.is_open:
            raise ValueError("This request is already closed.")
        was_selected = self.status == self.SELECTED
        self.status = self.REJECTED
        self.save(update_fields=["status"])
        if by == "lawyer":
            Notification.send(
                self.case.client.account,
                f"{self.lawyer.name} declined case {self.case.case_number}."
                + (" You can choose another lawyer." if was_selected else ""),
                Notification.CASE_UPDATE, self.case,
            )
        else:
            Notification.send(
                self.lawyer.account,
                f"Your request for case {self.case.case_number} was declined.",
                Notification.CASE_UPDATE, self.case,
            )
        return self

    def send_message(self, sender, body="", attachment=None):
        """Safe-mode chat between the client and this lawyer (text and/or one file)."""
        from notifications.models import Notification

        client_account = self.case.client.account
        lawyer_account = self.lawyer.account
        if sender.id not in (client_account.id, lawyer_account.id):
            raise PermissionError("You are not part of this conversation.")
        if self.status in (self.REJECTED, self.WITHDRAWN):
            raise ValueError("This conversation is closed.")
        body = (body or "").strip()
        if not body and attachment is None:
            raise ValueError("Please write a message or attach a file.")
        if attachment is not None:
            validate_attachment(attachment)
        if self.status != self.ACCEPTED:
            body = hide_contact_details(body)

        message = Message(request=self, sender=sender, body=body)
        if attachment is not None:
            message.attachment_name = os.path.basename(attachment.name)[:200]
            message.attachment = attachment
        message.save()

        recipient = lawyer_account if sender.id == client_account.id else client_account
        if not Notification.objects.filter(
            account=recipient, case=self.case, type=Notification.NEW_MESSAGE, is_read=False
        ).exists():
            Notification.send(
                recipient, f"You have a new message about case {self.case.case_number}.",
                Notification.NEW_MESSAGE, self.case,
            )
        return message


class Message(models.Model):
    """Chat message between a client and a lawyer who requested the client's case."""

    request = models.ForeignKey(CaseRequest, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="sent_messages")
    body = models.TextField(blank=True)
    attachment = models.FileField(upload_to="chat/%Y/%m/", storage=private_storage, blank=True)
    attachment_name = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender.email}: {self.body[:30]}"

    @property
    def is_image(self):
        return bool(self.attachment) and os.path.splitext(self.attachment.name)[1].lower() in IMAGE_EXTENSIONS

    def as_dict(self, viewer):
        """JSON used by the live chat."""
        from django.urls import reverse

        data = None
        if self.attachment:
            try:
                size = self.attachment.size
            except OSError:
                size = 0
            data = {"name": self.attachment_name or os.path.basename(self.attachment.name),
                    "url": reverse("cases:message_file", args=[self.pk]),
                    "is_image": self.is_image, "size": size}
        return {"id": self.pk, "mine": self.sender_id == viewer.id, "body": self.body, "file": data,
                "time": timezone.localtime(self.created_at).strftime("%d %b, %H:%M")}


class Review(models.Model):
    """Client's rating of the lawyer after the case is solved."""

    case = models.OneToOneField(Case, on_delete=models.CASCADE, related_name="review")
    lawyer = models.ForeignKey("accounts.Lawyer", on_delete=models.CASCADE, related_name="reviews")
    client = models.ForeignKey("accounts.Client", on_delete=models.CASCADE, related_name="reviews")
    rating = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.rating}/5 for {self.lawyer.name} ({self.case.case_number})"

    @classmethod
    def submit_review(cls, case, client, rating, comment=""):
        from notifications.models import Notification

        if case.client_id != client.id:
            raise PermissionError("This case does not belong to you.")
        if case.status != Case.CLOSED or case.assigned_lawyer_id is None:
            raise ValueError("You can review the lawyer only after the case is solved.")
        if cls.objects.filter(case=case).exists():
            raise ValueError("You have already reviewed this case.")
        review = cls.objects.create(case=case, lawyer=case.assigned_lawyer, client=client,
                                    rating=rating, comment=comment.strip())
        Notification.send(
            case.assigned_lawyer.account,
            f"You received a {rating}-star review for case {case.case_number}.",
            Notification.CASE_UPDATE, case,
        )
        return review


class WithdrawalRequest(models.Model):
    """The assigned lawyer asks to leave a running case. The client decides; no answer = automatic approval."""

    PENDING = "pending"
    APPROVED = "approved"
    DECLINED = "declined"
    AUTO = "auto"
    STATUS_CHOICES = [
        (PENDING, "Waiting for the client"),
        (APPROVED, "Approved by the client"),
        (DECLINED, "Declined by the client"),
        (AUTO, "Approved automatically"),
    ]

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="withdrawals")
    lawyer = models.ForeignKey("accounts.Lawyer", on_delete=models.CASCADE, related_name="withdrawals")
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    auto_at = models.DateTimeField()  # when the withdrawal happens automatically
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.lawyer.name} leaves {self.case.case_number} ({self.status})"

    @property
    def is_pending(self):
        return self.status == self.PENDING

    @classmethod
    def submit(cls, case, lawyer, reason):
        from notifications.models import Notification

        if case.assigned_lawyer_id != lawyer.id or case.status != Case.ASSIGNED:
            raise ValueError("You can only withdraw from a case that is currently assigned to you.")
        reason = (reason or "").strip()
        if len(reason) < 10:
            raise ValueError("Please explain the reason (at least 10 characters).")
        if cls.objects.filter(case=case, lawyer=lawyer, status=cls.PENDING).exists():
            raise ValueError("You already asked to withdraw from this case.")
        hours = getattr(settings, "WITHDRAWAL_AUTO_HOURS", 72)
        item = cls.objects.create(case=case, lawyer=lawyer, reason=reason,
                                  auto_at=timezone.now() + timedelta(hours=hours))
        when = timezone.localtime(item.auto_at).strftime("%d %b %Y, %H:%M")
        Notification.send(
            case.client.account,
            f"{lawyer.name} asked to withdraw from case {case.case_number}. Please respond. "
            f"If you do not answer, the withdrawal happens automatically on {when}.",
            Notification.CASE_UPDATE, case,
        )
        return item

    @transaction.atomic
    def approve(self, auto=False):
        from notifications.models import Notification

        if not self.is_pending:
            raise ValueError("This request was already answered.")
        self.status = self.AUTO if auto else self.APPROVED
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_at"])
        self.case.release_lawyer()
        Notification.send(
            self.lawyer.account,
            f"Your withdrawal from case {self.case.case_number} was "
            + ("approved automatically." if auto else "approved by the client."),
            Notification.CASE_UPDATE, self.case,
        )
        Notification.send(
            self.case.client.account,
            f"{self.lawyer.name} has left case {self.case.case_number}. The case is open for new lawyers again.",
            Notification.CASE_UPDATE, self.case,
        )
        return self

    def decline(self):
        from notifications.models import Notification

        if not self.is_pending:
            raise ValueError("This request was already answered.")
        self.status = self.DECLINED
        self.decided_at = timezone.now()
        self.save(update_fields=["status", "decided_at"])
        Notification.send(
            self.lawyer.account,
            f"The client declined your withdrawal request for case {self.case.case_number}. You stay on the case.",
            Notification.CASE_UPDATE, self.case,
        )
        return self


class Deadline(models.Model):
    """A time limit set by the court (hearing, document submission, payment...). Visible to the client."""

    case = models.ForeignKey(Case, on_delete=models.CASCADE, related_name="deadlines")
    title = models.CharField(max_length=200)
    due_at = models.DateTimeField()
    note = models.TextField(blank=True)
    created_by = models.ForeignKey("accounts.Account", on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="deadlines_set")
    created_at = models.DateTimeField(auto_now_add=True)
    reminded = models.BooleanField(default=False)

    class Meta:
        ordering = ["due_at"]

    def __str__(self):
        return f"{self.title} ({self.case.case_number})"

    @property
    def is_overdue(self):
        return self.due_at < timezone.now()

    def _recipients(self):
        accounts = []
        if self.case.client_notified:
            accounts.append(self.case.client.account)
        if self.case.assigned_lawyer_id:
            accounts.append(self.case.assigned_lawyer.account)
        return accounts

    @classmethod
    def add(cls, case, title, due_at, note="", by=None):
        from notifications.models import Notification

        item = cls.objects.create(case=case, title=title, due_at=due_at, note=note, created_by=by)
        when = timezone.localtime(due_at).strftime("%d %b %Y, %H:%M")
        for account in item._recipients():
            Notification.send(
                account, f"New deadline for case {case.case_number}: {title} (due {when}).",
                Notification.DEADLINE, case,
            )
        return item

    def remind(self):
        from notifications.models import Notification

        when = timezone.localtime(self.due_at).strftime("%d %b %Y, %H:%M")
        for account in self._recipients():
            Notification.send(
                account, f"Reminder: {self.title} for case {self.case.case_number} is due on {when}.",
                Notification.DEADLINE, self.case,
            )
        self.reminded = True
        self.save(update_fields=["reminded"])
