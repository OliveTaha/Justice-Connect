"""notifications app: Notification (class 9 of the UML)."""
from django.db import models


class Notification(models.Model):
    ACCOUNT_VERIFICATION = "account_verification"
    NEW_CASE = "new_case"
    CONSENT_REQUEST = "consent_request"
    LAWYER_REQUEST = "lawyer_request"
    LAWYER_ASSIGNED = "lawyer_assigned"
    CASE_UPDATE = "case_update"
    NEW_MESSAGE = "new_message"
    DEADLINE = "deadline"
    TYPE_CHOICES = [
        (ACCOUNT_VERIFICATION, "Account Verification"),
        (NEW_CASE, "New Case"),
        (CONSENT_REQUEST, "Consent Request"),
        (LAWYER_REQUEST, "Lawyer Request"),
        (LAWYER_ASSIGNED, "Lawyer Assigned"),
        (CASE_UPDATE, "Case Update"),
        (NEW_MESSAGE, "New Message"),
        (DEADLINE, "Deadline"),
    ]

    account = models.ForeignKey("accounts.Account", on_delete=models.CASCADE, related_name="notifications")
    case = models.ForeignKey("cases.Case", on_delete=models.CASCADE, null=True, blank=True, related_name="notifications")
    message = models.TextField()
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.get_type_display()}] {self.account.email}"

    @classmethod
    def send(cls, account, message, type, case=None):
        notification = cls.objects.create(account=account, message=message, type=type, case=case)
        if type == cls.ACCOUNT_VERIFICATION:
            # Simulated SMS / email: shown in the terminal that runs the server
            print(f"\n[SIMULATED SMS/EMAIL to {account.email}] {message}\n")
        return notification

    def mark_as_read(self):
        if not self.is_read:
            self.is_read = True
            self.save(update_fields=["is_read"])
        return self
