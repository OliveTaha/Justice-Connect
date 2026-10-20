from django import forms

from .models import Area, Case, Court


class CaseRegisterForm(forms.Form):
    court = forms.ModelChoiceField(queryset=Court.objects.select_related("area"), empty_label="Select court")
    case_number = forms.CharField(max_length=50)
    title = forms.CharField(max_length=200)
    case_type = forms.ChoiceField(choices=Case.TYPE_CHOICES)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))
    client_name = forms.CharField(max_length=150, label="Client full name")
    client_email = forms.EmailField(label="Client email")
    client_phone = forms.CharField(max_length=30, required=False, label="Client phone")
    client_address = forms.CharField(max_length=255, required=False, label="Client address")
    notify_client = forms.BooleanField(
        required=False, initial=True, label="Notify the client now",
        help_text="Untick for sensitive cases (for example serious criminal cases) where the "
                  "person should not be alerted. You can send the notification later from the case page.",
    )

    def clean_case_number(self):
        number = self.cleaned_data["case_number"].strip()
        if Case.objects.filter(case_number__iexact=number).exists():
            raise forms.ValidationError("A case with this number already exists.")
        return number


class CaseRequestForm(forms.Form):
    message = forms.CharField(
        max_length=1000, label="Message to the client",
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "Introduce yourself and explain how you can help..."}),
    )


class AreaForm(forms.ModelForm):
    class Meta:
        model = Area
        fields = ["name"]


class CourtForm(forms.ModelForm):
    class Meta:
        model = Court
        fields = ["name", "address", "area"]


class MessageForm(forms.Form):
    body = forms.CharField(
        max_length=2000, required=False, label="Message",
        widget=forms.Textarea(attrs={"rows": 1, "placeholder": "Write a message..."}),
    )
    attachment = forms.FileField(required=False)

    def clean(self):
        cleaned = super().clean()
        if not (cleaned.get("body") or "").strip() and not cleaned.get("attachment"):
            raise forms.ValidationError("Please write a message or attach a file.")
        return cleaned


class WithdrawalForm(forms.Form):
    reason = forms.CharField(
        min_length=10, max_length=1000, label="Reason for leaving the case",
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Explain why you cannot continue with this case..."}),
    )


class DeadlineForm(forms.Form):
    title = forms.CharField(max_length=200, label="What must be done?",
                            widget=forms.TextInput(attrs={"placeholder": "e.g. Submit documents / Hearing / Pay fee"}))
    due_at = forms.DateTimeField(
        label="Due date and time", input_formats=["%Y-%m-%dT%H:%M"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )
    note = forms.CharField(required=False, max_length=500, label="Note (optional)", widget=forms.Textarea(attrs={"rows": 2}))

    def clean_due_at(self):
        from django.utils import timezone

        value = self.cleaned_data["due_at"]
        if value <= timezone.now():
            raise forms.ValidationError("Choose a time in the future.")
        return value


class ReviewForm(forms.Form):
    rating = forms.IntegerField(min_value=1, max_value=5)
    comment = forms.CharField(required=False, max_length=1000, widget=forms.Textarea(attrs={"rows": 3}))
