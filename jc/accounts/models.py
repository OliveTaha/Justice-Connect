"""accounts app: Account, Client, Lawyer, Profile (classes 1-4 of the UML)."""
import uuid

from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.urls import reverse
from django.utils import timezone


class AccountManager(BaseUserManager):
    use_in_migrations = True

    def _make_username(self, email):
        base = email.split("@")[0][:140] or "user"
        username, n = base, 1
        while self.model.objects.filter(username=username).exists():
            n += 1
            username = f"{base}{n}"
        return username

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        extra_fields.setdefault("username", self._make_username(email))
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()  # e.g. client accounts created by a court
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", Account.ADMIN)
        extra_fields.setdefault("is_verified", True)
        return self.create_user(email, password, **extra_fields)


class Account(AbstractUser):
    """Handles authentication + role. Court Staff and Admin only use this class."""

    CLIENT = "client"
    LAWYER = "lawyer"
    COURT_STAFF = "court_staff"
    ADMIN = "admin"
    ROLE_CHOICES = [
        (CLIENT, "Client"),
        (LAWYER, "Lawyer"),
        (COURT_STAFF, "Court Staff"),
        (ADMIN, "Admin"),
    ]

    email = models.EmailField(unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=CLIENT)
    is_verified = models.BooleanField(default=False)  # isVerified
    guide_seen = models.BooleanField(default=False)  # user manual: "don't show again"
    deleted_at = models.DateTimeField(null=True, blank=True)  # set when the person deletes the account
    verification_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    objects = AccountManager()

    def __str__(self):
        return f"{self.email} ({self.get_role_display()})"

    # ---- UML methods ----
    @classmethod
    def register(cls, email, password=None, role=CLIENT, **extra):
        return cls.objects.create_user(email=email, password=password, role=role, **extra)

    def login(self, request):
        auth_login(request, self, backend="django.contrib.auth.backends.ModelBackend")

    @staticmethod
    def logout(request):
        auth_logout(request)

    def verify_account(self):
        self.is_verified = True
        self.save(update_fields=["is_verified"])
        return self

    @classmethod
    def for_client_email(cls, email):
        """Find / create the client account for a case. A deleted account is re-opened (history is kept)."""
        email = email.strip().lower()
        account = cls.objects.filter(email__iexact=email).first()
        if account is None:
            return cls.register(email=email, password=None, role=cls.CLIENT)
        if account.role != cls.CLIENT:
            raise ValueError("This email already belongs to a non-client account.")
        if not account.is_active:
            account.reactivate()
        return account

    def delete_account(self):
        """Soft delete: the person cannot log in any more, but the case history stays in the system."""
        self.is_active = False
        self.is_verified = False
        self.deleted_at = timezone.now()
        self.set_unusable_password()
        self.save()
        profile = getattr(self, "profile", None)
        if profile is not None:
            profile.photo.delete(save=False)
            profile.bio = ""
            profile.save()

    def reactivate(self):
        """The same person is registered in a new case: old records are linked again, new verification needed."""
        self.is_active = True
        self.deleted_at = None
        self.is_verified = False
        self.guide_seen = False
        self.verification_token = uuid.uuid4()
        self.set_unusable_password()
        self.save()

    # ---- helpers ----
    @property
    def verification_path(self):
        return reverse("accounts:verify", args=[self.verification_token])

    @property
    def verification_url(self):
        return settings.SITE_URL + reverse("accounts:verify", args=[self.verification_token])

    @property
    def display_name(self):
        for rel in ("client", "lawyer", "profile"):
            obj = getattr(self, rel, None)  # missing reverse one-to-one -> None
            if obj is not None and getattr(obj, "name", ""):
                return obj.name
        return self.username


class Client(models.Model):
    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="client")
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=30, blank=True)
    address = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name

    def view_case(self):
        # cases the court has not told the client about stay hidden
        return self.cases.filter(client_notified=True).select_related("court", "assigned_lawyer")

    def confirm_case(self, case):
        """Client gives consent so the case becomes visible to lawyers."""
        if case.client_id != self.id:
            raise PermissionError("This case does not belong to you.")
        return case.confirm_case()

    def select_lawyer(self, case_request):
        """Client selects a lawyer. The lawyer still has to give the final acceptance."""
        if case_request.case.client_id != self.id:
            raise PermissionError("This case does not belong to you.")
        return case_request.select()

    def reject_request(self, case_request):
        if case_request.case.client_id != self.id:
            raise PermissionError("This case does not belong to you.")
        return case_request.reject(by="client")

    @property
    def total_cases(self):
        return self.cases.count()

    @property
    def solved_cases(self):
        from cases.models import Case

        return self.cases.filter(status=Case.CLOSED).count()

    @property
    def unsolved_visible_cases(self):
        """Cases the client already knows about and that are not solved yet."""
        from cases.models import Case

        return self.cases.filter(client_notified=True).exclude(status=Case.CLOSED).count()

    def respond_withdrawal(self, withdrawal, approve):
        if withdrawal.case.client_id != self.id:
            raise PermissionError("This case does not belong to you.")
        return withdrawal.approve() if approve else withdrawal.decline()

    def review_lawyer(self, case, rating, comment=""):
        from cases.models import Review

        return Review.submit_review(case=case, client=self, rating=rating, comment=comment)


class Lawyer(models.Model):
    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="lawyer")
    name = models.CharField(max_length=150)
    bar_id = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return f"{self.name} ({self.bar_id})"

    def view_cases(self):
        from cases.models import Case

        return Case.objects.available().select_related("court")

    def request_case(self, case, message=""):
        from cases.models import CaseRequest

        return CaseRequest.submit_request(case=case, lawyer=self, message=message)

    def accept_request(self, case_request):
        """Final acceptance after the client selected this lawyer."""
        if case_request.lawyer_id != self.id:
            raise PermissionError("This request is not yours.")
        return case_request.accept()

    def decline_request(self, case_request):
        """Lawyer can decline (e.g. suspicious client) any time before final acceptance."""
        if case_request.lawyer_id != self.id:
            raise PermissionError("This request is not yours.")
        return case_request.reject(by="lawyer")

    def close_case(self, case):
        if case.assigned_lawyer_id != self.id:
            raise PermissionError("You are not the assigned lawyer of this case.")
        return case.close_case()

    def request_withdrawal(self, case, reason):
        from cases.models import WithdrawalRequest

        return WithdrawalRequest.submit(case=case, lawyer=self, reason=reason)

    @property
    def cases_solved(self):
        from cases.models import Case

        return self.assigned_cases.filter(status=Case.CLOSED).count()

    @property
    def review_count(self):
        return self.reviews.count()

    @property
    def average_rating(self):
        from django.db.models import Avg

        value = self.reviews.aggregate(avg=Avg("rating"))["avg"]
        return round(value, 1) if value else None


class Profile(models.Model):
    """Shared by clients and lawyers (specialization/experience are lawyer-only)."""

    account = models.OneToOneField(Account, on_delete=models.CASCADE, related_name="profile")
    name = models.CharField(max_length=150)
    location = models.CharField(max_length=150, blank=True)
    specialization = models.CharField(max_length=150, blank=True)
    experience = models.PositiveIntegerField(default=0, help_text="Years of experience")
    bio = models.TextField(blank=True)
    photo = models.ImageField(upload_to="profiles/%Y/%m/", blank=True)

    def __str__(self):
        return f"Profile of {self.name}"

    def view_profile(self):
        return self

    def update_profile(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)
        self.save()
        return self
