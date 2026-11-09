"""The platform's pages: the admin, logging in and out, a record's chart, its sensitivity
and eclipses, and comparisons."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from research_platform import eclipses_views, sensitivity_views, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("records/<int:pk>/chart/", views.record_chart, name="record-chart"),
    path("records/<int:pk>/chart/pairs/", views.record_chart_pairs, name="record-chart-pairs"),
    path("records/<int:pk>/chart.json", views.record_chart_json, name="record-chart-json"),
    path("records/<int:a>/compare/<int:b>/", views.record_compare, name="record-compare"),
    path(
        "records/<int:a>/compare/<int:b>/pairs/",
        views.record_compare_pairs,
        name="record-compare-pairs",
    ),
    path(
        "records/<int:pk>/sensitivity/",
        sensitivity_views.record_sensitivity,
        name="record-sensitivity",
    ),
    path(
        "records/<int:pk>/sensitivity/series/",
        sensitivity_views.record_sensitivity_series,
        name="record-sensitivity-series",
    ),
    path(
        "records/<int:pk>/sensitivity/detail/",
        sensitivity_views.record_sensitivity_detail,
        name="record-sensitivity-detail",
    ),
    path("records/<int:pk>/eclipses/", eclipses_views.record_eclipses, name="record-eclipses"),
    path(
        "records/<int:pk>/sensitivity.csv",
        sensitivity_views.record_sensitivity_csv,
        name="record-sensitivity-csv",
    ),
]
