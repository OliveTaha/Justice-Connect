from django.urls import path
from django.views.generic import RedirectView

from . import views

app_name = "accounts"

urlpatterns = [
    path("", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("register/done/<uuid:token>/", views.register_done_view, name="register_done"),
    path("verify/<uuid:token>/", views.verify_view, name="verify"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("profile/", RedirectView.as_view(pattern_name="accounts:settings"), name="profile"),
    path("guide-done/", views.guide_done, name="guide_done"),
    path("language/<str:code>/", views.set_language, name="language"),
    path("lawyers/", views.lawyers_list, name="lawyers"),
    path("lawyers/<int:pk>/", views.lawyer_detail, name="lawyer_detail"),
]
