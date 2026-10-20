from django.urls import path

from . import views

app_name = "cases"

urlpatterns = [
    # court staff / admin
    path("register/", views.case_register, name="register"),
    path("manage/", views.case_manage, name="manage"),
    path("courts/", views.court_manage, name="courts"),
    path("<int:pk>/notify-client/", views.notify_client, name="notify_client"),
    path("<int:pk>/close/", views.close_case, name="close"),
    # client
    path("mine/", views.my_cases, name="my_cases"),
    path("<int:pk>/consent/", views.give_consent, name="consent"),
    path("<int:pk>/review/", views.submit_review, name="review"),
    path("requests/<int:pk>/select/", views.select_lawyer, name="select_lawyer"),
    path("requests/<int:pk>/reject/", views.reject_request, name="reject_request"),
    # lawyer
    path("available/", views.available_cases, name="available"),
    path("requests/", views.my_requests, name="my_requests"),
    path("<int:pk>/request/", views.request_case, name="request_case"),
    path("requests/<int:pk>/accept/", views.lawyer_accept, name="lawyer_accept"),
    path("requests/<int:pk>/decline/", views.lawyer_decline, name="lawyer_decline"),
    path("<int:pk>/withdraw/", views.request_withdrawal, name="request_withdrawal"),
    # client: answer a withdrawal request
    path("withdrawals/<int:pk>/respond/", views.respond_withdrawal, name="respond_withdrawal"),
    # court staff: deadlines
    path("<int:pk>/deadlines/add/", views.add_deadline, name="add_deadline"),
    path("deadlines/<int:pk>/delete/", views.delete_deadline, name="delete_deadline"),
    # chat + shared
    path("requests/<int:pk>/chat/", views.request_chat, name="chat"),
    path("requests/<int:pk>/chat/poll/", views.chat_poll, name="chat_poll"),
    path("messages/<int:pk>/file/", views.message_file, name="message_file"),
    path("<int:pk>/", views.case_detail, name="detail"),
]
