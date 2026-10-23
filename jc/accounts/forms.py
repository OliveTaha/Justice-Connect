from django import forms
from django.contrib.auth.password_validation import validate_password

from .models import Account, Lawyer, Profile


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={"placeholder": "Enter your email", "autofocus": True}))
    password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Enter your password"}))
    remember = forms.BooleanField(required=False, label="Remember me")


class _PasswordPairMixin:
    def clean(self):
        cleaned = super().clean()
        pw, confirm = cleaned.get("password"), cleaned.get("confirm_password")
        if pw and confirm and pw != confirm:
            self.add_error("confirm_password", "Passwords do not match.")
        elif pw:
            try:
                validate_password(pw)
            except forms.ValidationError as e:
                self.add_error("password", e)
        return cleaned


class LawyerRegisterForm(_PasswordPairMixin, forms.Form):
    name = forms.CharField(max_length=150, label="Full name")
    email = forms.EmailField()
    bar_id = forms.CharField(max_length=50, label="Bar council ID")
    password = forms.CharField(widget=forms.PasswordInput)
    confirm_password = forms.CharField(widget=forms.PasswordInput)

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if Account.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account with this email already exists.")
        return email

    def clean_bar_id(self):
        bar_id = self.cleaned_data["bar_id"].strip()
        if Lawyer.objects.filter(bar_id__iexact=bar_id).exists():
            raise forms.ValidationError("This Bar ID is already registered.")
        return bar_id


class VerifyPasswordForm(_PasswordPairMixin, forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="Create a password")
    confirm_password = forms.CharField(widget=forms.PasswordInput)


class ProfileForm(forms.ModelForm):
    photo = forms.ImageField(required=False, label="Profile photo", widget=forms.FileInput(attrs={"accept": "image/*", "id": "photoInput", "data-big": "The photo is too large (maximum 2 MB)."}))
    remove_photo = forms.BooleanField(required=False, label="Remove current photo")

    class Meta:
        model = Profile
        fields = ["name", "location", "specialization", "experience", "bio", "photo"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, role=None, **kwargs):
        super().__init__(*args, **kwargs)
        if role != Account.LAWYER:  # specialization / experience are for lawyers only
            self.fields.pop("specialization")
            self.fields.pop("experience")

    def clean_photo(self):
        photo = self.cleaned_data.get("photo")
        if photo and hasattr(photo, "size") and photo.size > 2 * 1024 * 1024:
            raise forms.ValidationError("The photo is too large (maximum 2 MB).")
        return photo

    def save(self, commit=True):
        profile = super().save(commit=False)
        if self.cleaned_data.get("remove_photo") and not self.files.get("photo"):
            profile.photo.delete(save=False)
        if commit:
            profile.save()
        return profile


class DeleteAccountForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput, label="Your password")

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        pw = self.cleaned_data["password"]
        if not self.user.check_password(pw):
            raise forms.ValidationError("Password is incorrect.")
        return pw
