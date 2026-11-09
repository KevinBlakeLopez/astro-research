"""The platform's pages: the admin, logging in and out, and a record's chart."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from research_platform import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("records/<int:pk>/chart/", views.record_chart, name="record-chart"),
    path("records/<int:pk>/chart/pairs/", views.record_chart_pairs, name="record-chart-pairs"),
    path("records/<int:pk>/chart.json", views.record_chart_json, name="record-chart-json"),
]
