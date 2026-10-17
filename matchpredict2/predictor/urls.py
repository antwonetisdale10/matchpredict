from django.contrib.auth import views as auth
from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("matches/", views.matches, name="matches"),
    path("match/<int:pk>/", views.match_detail, name="match"),
    path("match/<int:pk>/pick/", views.pick_form, name="pick"),
    path("table/", views.table, name="table"),
    path("teams/", views.teams, name="teams"),
    path("team/<slug:slug>/", views.team_detail, name="team"),
    path("players/", views.players, name="players"),
    path("leaderboard/", views.leaderboard, name="leaderboard"),
    path("me/", views.profile, name="profile"),
    path("accuracy/", views.accuracy, name="accuracy"),
    path("studio/", views.studio, name="studio"),
    path("studio/job/<str:jid>/", views.job_status, name="job"),
    path("studio/result/<int:pk>/", views.record_result, name="record_result"),
    path("studio/sample/<str:kind>.csv", views.sample_csv, name="sample"),
    path("login/", auth.LoginView.as_view(), name="login"),
    path("logout/", auth.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
]
