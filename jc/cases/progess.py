"""Builds the progress bar, step list and activity timeline shown on the case page."""
from .models import Case, CaseRequest, WithdrawalRequest


def build_progress(case, viewer):
    requests = list(case.requests.select_related("lawyer"))
    first_request = min((r.requested_at for r in requests), default=None)
    selected = any(r.status == CaseRequest.SELECTED for r in requests)

    if case.status == Case.CLOSED:
        percent, stage, hint = 100, "Solved", "This case is solved."
    elif case.status == Case.ASSIGNED:
        percent, stage, hint = 85, "Lawyer assigned", "The case is in progress with the assigned lawyer."
    elif selected:
        percent, stage, hint = 70, "Final acceptance", "Waiting for the selected lawyer's final acceptance."
    elif case.consent and requests:
        percent, stage, hint = 55, "Lawyer requests", "Chat with the lawyers and select one."
    elif case.consent:
        percent, stage, hint = 40, "Open for lawyers", "Waiting for lawyers to send requests."
    elif case.client_notified:
        percent, stage, hint = 20, "Waiting for consent", "Waiting for the client's consent."
    else:
        percent, stage, hint = 8, "Registered", "Registered by the court. The client has not been notified yet."

    if case.status == Case.CLOSED:
        current = 5
    elif case.status == Case.ASSIGNED:
        current = 4
    elif case.consent:
        current = 2
    else:
        current = 1
    steps = [
        ("Registered", case.created_at, "Registered by the court"),
        ("Client consent", case.consent_date, "The client lets lawyers see the case"),
        ("Lawyer requests", first_request, "Lawyers send requests and chat in safe mode"),
        ("Lawyer assigned", case.assigned_at, "Client selects, lawyer gives the final acceptance"),
        ("Solved", case.closed_at, "Case solved and the lawyer is reviewed"),
    ]
    step_rows = [{"label": label, "when": when, "desc": desc,
                  "state": "done" if i < current else "current" if i == current else "todo"}
                 for i, (label, when, desc) in enumerate(steps)]

    # ---- activity timeline ----
    events = [(case.created_at, "folder", "Case registered", f"{case.court.name}")]
    if case.consent_date:
        events.append((case.consent_date, "check", "Client gave consent", "The case became visible to lawyers"))
    lawyer = getattr(viewer, "lawyer", None) if viewer.role == "lawyer" else None
    for r in requests:
        if viewer.role == "lawyer" and r.lawyer_id != getattr(lawyer, "id", None):
            continue
        events.append((r.requested_at, "send", "Lawyer request", r.lawyer.name))
    if case.assigned_at and case.assigned_lawyer:
        if viewer.role != "lawyer" or case.assigned_lawyer_id == getattr(lawyer, "id", None):
            events.append((case.assigned_at, "user", "Lawyer assigned", case.assigned_lawyer.name))
    for w in case.withdrawals.select_related("lawyer"):
        if viewer.role == "lawyer" and w.lawyer_id != getattr(lawyer, "id", None):
            continue
        events.append((w.created_at, "alert", "Withdrawal requested", w.lawyer.name))
        if w.decided_at:
            label = {"approved": "Withdrawal approved", "declined": "Withdrawal declined",
                     "auto": "Withdrawal approved automatically"}[w.status]
            events.append((w.decided_at, "info", label, w.lawyer.name))
    for d in case.deadlines.all():
        events.append((d.created_at, "clock", "Deadline set", d.title))
    if case.closed_at:
        events.append((case.closed_at, "award", "Case solved", ""))
    review = getattr(case, "review", None)
    if review:
        events.append((review.created_at, "star", "Client review", f"{review.rating} / 5"))
    events.sort(key=lambda e: e[0])
    timeline = [{"when": w, "icon": i, "title": t, "text": x} for w, i, t, x in events]

    return {"percent": percent, "stage": stage, "hint": hint, "steps": step_rows, "timeline": timeline}
