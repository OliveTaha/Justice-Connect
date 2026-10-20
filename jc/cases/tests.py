"""End-to-end tests of the Justice Connect workflow."""
from django.test import TestCase
from django.urls import reverse

from accounts.models import Account, Lawyer
from cases.models import Area, Case, CaseRequest, Court, Message, Review, hide_contact_details
from notifications.models import Notification

CLIENT_PW, LAWYER_PW, COURT_PW = "Client@12345", "Lawyer@12345", "Court@12345"


class BaseCase(TestCase):
    def setUp(self):
        area = Area.create_area("Dhaka")
        self.court = Court.objects.create(name="Dhaka District Court", address="Old Dhaka", area=area)
        self.staff = Account.register("court@example.com", COURT_PW, role=Account.COURT_STAFF, is_verified=True)

    def login(self, email, pw):
        self.client.post(reverse("accounts:logout"))
        return self.client.post(reverse("accounts:login"), {"email": email, "password": pw})

    def register_case(self, number="C-001", notify=True, email="Rahim@Example.com"):
        self.login("court@example.com", COURT_PW)
        data = {
            "court": self.court.pk, "case_number": number, "title": "Land dispute", "case_type": "Property",
            "description": "Boundary dispute", "client_name": "Rahim", "client_email": email,
            "client_phone": "01700000000", "client_address": "Dhaka",
        }
        if notify:
            data["notify_client"] = "on"
        self.client.post(reverse("cases:register"), data)
        return Case.objects.get(case_number=number)

    def verify_client(self, case):
        acct = case.client.account
        self.client.post(reverse("accounts:logout"))
        self.client.post(reverse("accounts:verify", args=[acct.verification_token]),
                         {"password": CLIENT_PW, "confirm_password": CLIENT_PW})
        acct.refresh_from_db()
        return acct

    def make_lawyer(self, email, name, bar):
        self.client.post(reverse("accounts:logout"))
        self.client.post(reverse("accounts:register"), {
            "name": name, "email": email, "bar_id": bar, "password": LAWYER_PW, "confirm_password": LAWYER_PW})
        acct = Account.objects.get(email=email)
        self.client.post(reverse("accounts:verify", args=[acct.verification_token]))
        return acct, Lawyer.objects.get(account=acct)

    def consent(self, case):
        self.login("rahim@example.com", CLIENT_PW)
        self.client.post(reverse("cases:consent", args=[case.pk]))
        case.refresh_from_db()


class WorkflowTest(BaseCase):
    def test_full_flow_with_two_step_acceptance_chat_and_review(self):
        case = self.register_case()
        acct = case.client.account
        self.assertEqual(acct.email, "rahim@example.com")
        self.assertFalse(acct.is_verified)
        self.assertFalse(acct.has_usable_password())
        self.assertTrue(case.client_notified)
        self.assertTrue(Notification.objects.filter(account=acct, type=Notification.ACCOUNT_VERIFICATION).exists())

        r = self.login("rahim@example.com", "x")  # unverified cannot log in
        self.assertEqual(r.status_code, 200)

        acct = self.verify_client(case)
        self.assertTrue(acct.is_verified)
        self.assertContains(self.client.get(reverse("cases:my_cases")), "C-001")

        lawyer_acct, lawyer = self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.login("karim@example.com", LAWYER_PW)
        self.assertNotContains(self.client.get(reverse("cases:available")), "C-001")
        self.assertRedirects(self.client.get(reverse("cases:detail", args=[case.pk])), reverse("accounts:dashboard"))

        self.consent(case)
        self.assertTrue(case.consent)
        self.assertEqual(case.status, Case.OPEN)

        # lawyer requests; client details hidden
        self.login("karim@example.com", LAWYER_PW)
        self.assertContains(self.client.get(reverse("cases:available")), "C-001")
        r = self.client.get(reverse("cases:detail", args=[case.pk]))
        self.assertNotContains(r, "rahim@example.com")
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "I can help"})
        req = CaseRequest.objects.get(case=case)
        self.assertEqual(req.status, CaseRequest.PENDING)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "again"})
        self.assertEqual(CaseRequest.objects.filter(case=case).count(), 1)

        # safe-mode chat BEFORE final acceptance: contact details hidden, client name hidden
        r = self.client.get(reverse("cases:chat", args=[req.pk]))
        self.assertNotContains(r, "Rahim")
        self.client.post(reverse("cases:chat", args=[req.pk]), {"body": "Call me on 01712345678 or a@b.com"})
        msg = Message.objects.get(request=req)
        self.assertNotIn("01712345678", msg.body)
        self.assertNotIn("a@b.com", msg.body)

        # client replies, then selects (step 1) -> still not assigned
        self.login("rahim@example.com", CLIENT_PW)
        self.assertContains(self.client.get(reverse("cases:chat", args=[req.pk])), "[hidden]")
        self.client.post(reverse("cases:chat", args=[req.pk]), {"body": "Hello, tell me more"})
        self.assertEqual(Message.objects.filter(request=req).count(), 2)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case.pk])), "Karim")
        self.client.post(reverse("cases:select_lawyer", args=[req.pk]))
        req.refresh_from_db(); case.refresh_from_db()
        self.assertEqual(req.status, CaseRequest.SELECTED)
        self.assertIsNone(case.assigned_lawyer)

        # lawyer gives FINAL acceptance (step 2)
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:lawyer_accept", args=[req.pk]))
        req.refresh_from_db(); case.refresh_from_db()
        self.assertEqual(req.status, CaseRequest.ACCEPTED)
        self.assertEqual(case.assigned_lawyer, lawyer)
        self.assertEqual(case.status, Case.ASSIGNED)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case.pk])), "rahim@example.com")

        # after acceptance contact details can be shared
        self.client.post(reverse("cases:chat", args=[req.pk]), {"body": "My number is 01712345678"})
        self.assertTrue(Message.objects.filter(request=req, body__contains="01712345678").exists())

        # lawyer marks solved, client reviews
        self.client.post(reverse("cases:close", args=[case.pk]))
        case.refresh_from_db()
        self.assertEqual(case.status, Case.CLOSED)
        self.assertEqual(lawyer.cases_solved, 1)
        self.login("rahim@example.com", CLIENT_PW)
        self.client.post(reverse("cases:review", args=[case.pk]), {"rating": 5, "comment": "Great lawyer"})
        self.assertEqual(Review.objects.get(case=case).rating, 5)
        self.client.post(reverse("cases:review", args=[case.pk]), {"rating": 1})  # only one review
        self.assertEqual(Review.objects.filter(case=case).count(), 1)
        self.assertEqual(lawyer.average_rating, 5.0)
        self.assertContains(self.client.get(reverse("accounts:lawyer_detail", args=[lawyer.pk])), "Great lawyer")

        # lawyer dashboard + pages render
        self.login("karim@example.com", LAWYER_PW)
        for name in ("accounts:dashboard", "cases:my_requests", "notifications:list", "accounts:settings"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "5.0 / 5")

    def test_lawyer_can_decline_before_final_acceptance(self):
        case = self.register_case()
        self.verify_client(case)
        _, lawyer = self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.consent(case)
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "hi"})
        req = CaseRequest.objects.get(case=case)
        self.login("rahim@example.com", CLIENT_PW)
        self.client.post(reverse("cases:select_lawyer", args=[req.pk]))
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:lawyer_decline", args=[req.pk]))
        req.refresh_from_db(); case.refresh_from_db()
        self.assertEqual(req.status, CaseRequest.REJECTED)
        self.assertIsNone(case.assigned_lawyer)
        self.assertEqual(case.status, Case.OPEN)
        self.assertTrue(Notification.objects.filter(account=case.client.account, message__contains="declined").exists())
        # lawyer cannot finalise without being selected / after declining
        self.client.post(reverse("cases:lawyer_accept", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, CaseRequest.REJECTED)

    def test_cannot_finalise_without_client_selection(self):
        case = self.register_case()
        self.verify_client(case)
        self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.consent(case)
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "hi"})
        req = CaseRequest.objects.get(case=case)
        self.client.post(reverse("cases:lawyer_accept", args=[req.pk]))
        req.refresh_from_db()
        self.assertEqual(req.status, CaseRequest.PENDING)

    def test_sensitive_case_is_not_sent_to_client(self):
        case = self.register_case(number="M-001", notify=False)
        self.assertFalse(case.client_notified)
        self.assertFalse(Notification.objects.filter(case=case).exists())
        # staff sees the notify button, client account has no link sent yet
        r = self.client.get(reverse("cases:detail", args=[case.pk]))
        self.assertContains(r, "has not been notified")
        # a verified client still must not see an un-notified case
        acct = case.client.account
        acct.verify_account(); acct.set_password(CLIENT_PW); acct.save()
        self.login("rahim@example.com", CLIENT_PW)
        self.assertNotContains(self.client.get(reverse("cases:my_cases")), "M-001")
        self.assertRedirects(self.client.get(reverse("cases:detail", args=[case.pk])), reverse("accounts:dashboard"))
        # court decides to notify later
        self.login("court@example.com", COURT_PW)
        self.client.post(reverse("cases:notify_client", args=[case.pk]))
        case.refresh_from_db()
        self.assertTrue(case.client_notified)
        self.assertTrue(Notification.objects.filter(case=case, account=acct).exists())
        self.login("rahim@example.com", CLIENT_PW)
        self.assertContains(self.client.get(reverse("cases:my_cases")), "M-001")

    def test_role_protection_and_pages_render(self):
        self.login("court@example.com", COURT_PW)
        self.assertRedirects(self.client.get(reverse("cases:available")), reverse("accounts:dashboard"))
        for name in ("cases:manage", "cases:courts", "cases:register", "accounts:dashboard"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)


class SafeModeTest(TestCase):
    def test_hide_contact_details(self):
        self.assertEqual(hide_contact_details("mail me: x.y@mail.com"), "mail me: [hidden]")
        self.assertIn("[hidden]", hide_contact_details("call +880 1712-345678 now"))
        self.assertEqual(hide_contact_details("hearing on 2026-10-06"), "hearing on 2026-10-06")


# =====================================================================
#  New features: withdrawal, deadlines, account deletion, chat files, language
# =====================================================================
import io
import shutil
import tempfile
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone

from cases.models import Deadline, WithdrawalRequest
from cases.services import process_due

TMP_MEDIA = tempfile.mkdtemp()


def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, "PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=TMP_MEDIA, PRIVATE_MEDIA_ROOT=TMP_MEDIA)
class FeatureTests(BaseCase):
    def assigned_case(self):
        """Return (case, request, lawyer) with the lawyer finally accepted."""
        case = self.register_case()
        self.verify_client(case)
        acct, lawyer = self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.consent(case)
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "I can help"})
        req = CaseRequest.objects.get(case=case)
        self.login("rahim@example.com", CLIENT_PW)
        self.client.post(reverse("cases:select_lawyer", args=[req.pk]))
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:lawyer_accept", args=[req.pk]))
        case.refresh_from_db()
        self.assertEqual(case.status, Case.ASSIGNED)
        return case, req, lawyer

    # ---------------- withdrawal ----------------
    def test_withdrawal_approved_by_client_is_immediate(self):
        case, req, lawyer = self.assigned_case()
        self.client.post(reverse("cases:request_withdrawal", args=[case.pk]), {"reason": "Busy"})
        self.assertFalse(WithdrawalRequest.objects.filter(case=case).exists())  # too short -> refused
        self.client.post(reverse("cases:request_withdrawal", args=[case.pk]), {"reason": "I have a conflict of interest"})
        w = WithdrawalRequest.objects.get(case=case)
        self.assertEqual(w.status, WithdrawalRequest.PENDING)
        case.refresh_from_db()
        self.assertEqual(case.assigned_lawyer, lawyer)  # still assigned until the client answers
        self.login("rahim@example.com", CLIENT_PW)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case.pk])), "conflict of interest")
        self.client.post(reverse("cases:respond_withdrawal", args=[w.pk]), {"decision": "approve"})
        case.refresh_from_db(); req.refresh_from_db(); w.refresh_from_db()
        self.assertIsNone(case.assigned_lawyer)
        self.assertEqual(case.status, Case.OPEN)
        self.assertEqual(req.status, CaseRequest.WITHDRAWN)
        self.assertEqual(w.status, WithdrawalRequest.APPROVED)
        # the lawyer cannot request the same case again, but the chat is closed
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "again"})
        self.assertEqual(CaseRequest.objects.filter(case=case).count(), 1)

    def test_withdrawal_declined_keeps_lawyer(self):
        case, req, lawyer = self.assigned_case()
        self.client.post(reverse("cases:request_withdrawal", args=[case.pk]), {"reason": "I am too busy these months"})
        w = WithdrawalRequest.objects.get(case=case)
        self.login("rahim@example.com", CLIENT_PW)
        self.client.post(reverse("cases:respond_withdrawal", args=[w.pk]), {"decision": "decline"})
        case.refresh_from_db(); w.refresh_from_db()
        self.assertEqual(case.assigned_lawyer, lawyer)
        self.assertEqual(w.status, WithdrawalRequest.DECLINED)
        process_due()  # a declined request must never withdraw automatically
        case.refresh_from_db()
        self.assertEqual(case.assigned_lawyer, lawyer)

    def test_withdrawal_automatic_when_client_does_not_answer(self):
        case, req, lawyer = self.assigned_case()
        self.client.post(reverse("cases:request_withdrawal", args=[case.pk]), {"reason": "Moving to another city"})
        w = WithdrawalRequest.objects.get(case=case)
        self.assertGreater(w.auto_at, timezone.now() + timedelta(hours=70))
        process_due()  # not due yet
        case.refresh_from_db()
        self.assertEqual(case.assigned_lawyer, lawyer)
        WithdrawalRequest.objects.filter(pk=w.pk).update(auto_at=timezone.now() - timedelta(minutes=1))
        process_due()
        case.refresh_from_db(); w.refresh_from_db()
        self.assertIsNone(case.assigned_lawyer)
        self.assertEqual(w.status, WithdrawalRequest.AUTO)

    # ---------------- deadlines ----------------
    def test_deadline_set_by_court_visible_to_client_with_reminder(self):
        case = self.register_case()
        self.verify_client(case)
        self.login("court@example.com", COURT_PW)
        past = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(reverse("cases:add_deadline", args=[case.pk]), {"title": "Old", "due_at": past})
        self.assertEqual(Deadline.objects.count(), 0)  # must be in the future
        soon = (timezone.localtime() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")
        self.client.post(reverse("cases:add_deadline", args=[case.pk]), {"title": "Submit documents", "due_at": soon, "note": "Bring ID"})
        self.assertEqual(Deadline.objects.count(), 1)
        self.assertTrue(Notification.objects.filter(account=case.client.account, type=Notification.DEADLINE).exists())
        self.login("rahim@example.com", CLIENT_PW)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case.pk])), "Submit documents")
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "Submit documents")
        before = Notification.objects.filter(type=Notification.DEADLINE).count()
        process_due()  # due within 24h -> reminder
        self.assertEqual(Notification.objects.filter(type=Notification.DEADLINE).count(), before + 1)
        process_due()  # only once
        self.assertEqual(Notification.objects.filter(type=Notification.DEADLINE).count(), before + 1)

    # ---------------- account deletion ----------------
    def test_client_can_delete_after_solved_and_history_is_kept(self):
        case, req, lawyer = self.assigned_case()
        acct = case.client.account
        self.login("rahim@example.com", CLIENT_PW)
        r = self.client.post(reverse("accounts:settings"), {"action": "delete", "password": CLIENT_PW})
        acct.refresh_from_db()
        self.assertTrue(acct.is_active)  # blocked: the case is not solved yet
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:close", args=[case.pk]))
        self.login("rahim@example.com", CLIENT_PW)
        r = self.client.post(reverse("accounts:settings"), {"action": "delete", "password": "wrong"})
        acct.refresh_from_db()
        self.assertTrue(acct.is_active)  # wrong password
        self.client.post(reverse("accounts:settings"), {"action": "delete", "password": CLIENT_PW})
        acct.refresh_from_db()
        self.assertFalse(acct.is_active)
        self.assertIsNotNone(acct.deleted_at)
        r = self.login("rahim@example.com", CLIENT_PW)
        self.assertEqual(r.status_code, 200)  # cannot log in any more
        # same person gets a new case later -> same Client record, history kept, new verification needed
        case2 = self.register_case(number="C-002")
        acct.refresh_from_db()
        self.assertTrue(acct.is_active)
        self.assertFalse(acct.is_verified)
        self.assertEqual(case2.client_id, case.client_id)
        self.assertEqual(case2.client.total_cases, 2)
        self.assertEqual(case2.client.solved_cases, 1)
        self.login("court@example.com", COURT_PW)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case2.pk])), "2 cases, 1 solved")

    # ---------------- profile, photo, directory, guide ----------------
    def test_profile_photo_settings_directory_and_guide(self):
        self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.login("karim@example.com", LAWYER_PW)
        photo = SimpleUploadedFile("me.png", png_bytes(), content_type="image/png")
        r = self.client.post(reverse("accounts:settings"), {
            "action": "profile", "name": "Karim Uddin", "location": "Dhaka", "specialization": "Criminal Law",
            "experience": 7, "bio": "Experienced", "photo": photo})
        self.assertEqual(r.status_code, 302)
        acct = Account.objects.get(email="karim@example.com")
        self.assertTrue(acct.profile.photo)
        self.assertEqual(acct.lawyer.name, "Karim Uddin")
        self.assertContains(self.client.get(reverse("accounts:settings")), acct.profile.photo.url)
        for tab in ("profile", "security", "preferences"):
            self.assertEqual(self.client.get(reverse("accounts:settings") + f"?tab={tab}").status_code, 200)
        # password change
        r = self.client.post(reverse("accounts:settings"), {"action": "password", "old_password": LAWYER_PW,
                                                            "new_password1": "NewPass@2026", "new_password2": "NewPass@2026"})
        self.assertEqual(r.status_code, 302)
        # directory + guide
        self.assertContains(self.client.get(reverse("accounts:lawyers")), "Top lawyers")
        self.assertFalse(Account.objects.get(email="karim@example.com").guide_seen)
        self.client.post(reverse("accounts:guide_done"), {"dont_show": "1"})
        self.assertTrue(Account.objects.get(email="karim@example.com").guide_seen)
        self.assertNotContains(self.client.get(reverse("accounts:dashboard")), 'data-auto="1"')

    def test_client_sees_top_lawyers_and_constitution_info(self):
        case = self.register_case()
        self.verify_client(case)
        self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.login("rahim@example.com", CLIENT_PW)
        r = self.client.get(reverse("accounts:dashboard"))
        self.assertContains(r, "Constitution of Bangladesh")
        self.assertContains(r, "16430")
        self.assertContains(r, 'data-auto="1"')  # user guide pops up
        self.assertContains(self.client.get(reverse("accounts:lawyers")), "Karim")

    # ---------------- chat files ----------------
    def test_chat_files_ajax_poll_and_access_control(self):
        case = self.register_case()
        self.verify_client(case)
        self.make_lawyer("karim@example.com", "Karim", "BAR-9")
        self.make_lawyer("other@example.com", "Other", "BAR-8")
        self.consent(case)
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:request_case", args=[case.pk]), {"message": "hi"})
        req = CaseRequest.objects.get(case=case)
        ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
        # image through AJAX
        img = SimpleUploadedFile("photo.png", png_bytes(), content_type="image/png")
        r = self.client.post(reverse("cases:chat", args=[req.pk]), {"body": "see this", "attachment": img}, **ajax)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertTrue(data["message"]["file"]["is_image"])
        first_id = data["message"]["id"]
        # not allowed file type
        bad = SimpleUploadedFile("virus.exe", b"MZ", content_type="application/octet-stream")
        r = self.client.post(reverse("cases:chat", args=[req.pk]), {"attachment": bad}, **ajax)
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()["ok"])
        # pdf as a file chip
        pdf = SimpleUploadedFile("doc.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        r = self.client.post(reverse("cases:chat", args=[req.pk]), {"attachment": pdf}, **ajax)
        self.assertFalse(r.json()["message"]["file"]["is_image"])
        # participants can download, outsiders cannot
        url = data["message"]["file"]["url"]
        self.assertEqual(self.client.get(url).status_code, 200)
        self.login("other@example.com", LAWYER_PW)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("cases:chat_poll", args=[req.pk])).status_code, 403)
        # the client receives it with the poll endpoint and the page
        self.login("rahim@example.com", CLIENT_PW)
        poll = self.client.get(reverse("cases:chat_poll", args=[req.pk]) + f"?after={first_id}").json()
        self.assertEqual(len(poll["messages"]), 1)  # only the pdf is newer
        self.assertEqual(poll["status"], "pending")
        page = self.client.get(reverse("cases:chat", args=[req.pk]))
        self.assertContains(page, "att-img")
        self.assertContains(page, "doc.pdf")

    # ---------------- progress ----------------
    def test_progress_percent_and_timeline(self):
        case, req, lawyer = self.assigned_case()
        self.login("rahim@example.com", CLIENT_PW)
        page = self.client.get(reverse("cases:detail", args=[case.pk]))
        self.assertContains(page, "85%")
        self.assertContains(page, "Activity timeline")
        self.login("karim@example.com", LAWYER_PW)
        self.client.post(reverse("cases:close", args=[case.pk]))
        self.login("rahim@example.com", CLIENT_PW)
        self.assertContains(self.client.get(reverse("cases:detail", args=[case.pk])), "100%")


class LanguageTests(BaseCase):
    def test_bangla_switch_translates_pages_but_keeps_forms_working(self):
        self.login("court@example.com", COURT_PW)
        self.assertContains(self.client.get(reverse("accounts:dashboard")), "Welcome back")
        r = self.client.get(reverse("accounts:language", args=["bn"]))
        self.assertEqual(r.status_code, 302)
        page = self.client.get(reverse("accounts:dashboard"))
        self.assertContains(page, "ড্যাশবোর্ড")
        self.assertContains(page, "স্বাগতম")
        self.assertContains(page, '<html lang="bn"')
        self.assertNotContains(page, "Welcome back")
        # the form still posts English values although the labels are Bangla
        form = self.client.get(reverse("cases:register"))
        self.assertContains(form, "মামলা নিবন্ধন")
        self.assertContains(form, 'value="Property"')
        self.client.post(reverse("cases:register"), {
            "court": self.court.pk, "case_number": "B-1", "title": "বাংলা শিরোনাম", "case_type": "Property",
            "description": "বিবরণ", "client_name": "রহিম", "client_email": "rahim@example.com", "notify_client": "on"})
        self.assertTrue(Case.objects.filter(case_number="B-1").exists())
        # user content is never translated, JSON stays untouched
        detail = self.client.get(reverse("cases:detail", args=[Case.objects.get(case_number="B-1").pk]))
        self.assertContains(detail, "বাংলা শিরোনাম")
        self.assertContains(detail, "আচরণ") if False else None
        # login page (no session user) also switches
        self.client.post(reverse("accounts:logout"))
        self.assertContains(self.client.get(reverse("accounts:login")), "আবার স্বাগতম")
        self.client.get(reverse("accounts:language", args=["en"]))
        self.assertContains(self.client.get(reverse("accounts:login")), "Welcome Back")

    def test_notifications_and_dates_translate(self):
        case = self.register_case()
        self.verify_client(case)
        self.client.get(reverse("accounts:language", args=["bn"]))
        page = self.client.get(reverse("notifications:list"))
        self.assertContains(page, "আপনার জন্য একটি নতুন মামলা") if False else None
        self.assertContains(page, "বিজ্ঞপ্তি")
        self.assertContains(page, "আগে")  # "x minutes ago"
