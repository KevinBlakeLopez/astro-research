"""The platform's pages: the admin, logging in and out, a record's chart, its sensitivity,
eclipses and parans, comparisons, datasets with their runs and stored charts, and features."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from research_platform import (
    eclipses_views,
    features_views,
    parans_views,
    runs_views,
    sensitivity_views,
    views,
)

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
    path("records/<int:pk>/parans/", parans_views.record_parans, name="record-parans"),
    path(
        "records/<int:pk>/parans/events/",
        parans_views.record_parans_events,
        name="record-parans-events",
    ),
    path(
        "records/<int:pk>/parans/gaps/", parans_views.record_parans_gaps, name="record-parans-gaps"
    ),
    path(
        "records/<int:pk>/parans/stars/",
        parans_views.record_parans_stars,
        name="record-parans-stars",
    ),
    path("records/<int:pk>/parans.csv", parans_views.record_parans_csv, name="record-parans-csv"),
    path(
        "records/<int:pk>/sensitivity.csv",
        sensitivity_views.record_sensitivity_csv,
        name="record-sensitivity-csv",
    ),
    path("datasets/", runs_views.datasets, name="datasets"),
    path("datasets/<int:pk>/", runs_views.dataset, name="dataset"),
    path("datasets/<int:pk>/run/", runs_views.dataset_run, name="dataset-run"),
    path("runs/<int:pk>/", runs_views.run, name="run"),
    path("runs/<int:pk>/outcomes.csv", runs_views.run_outcomes_csv, name="run-outcomes-csv"),
    path("runs/<int:pk>/charts.jsonl", runs_views.run_charts_jsonl, name="run-charts-jsonl"),
    path(
        "runs/<int:pk>/chart-values.csv",
        runs_views.run_chart_values_csv,
        name="run-chart-values-csv",
    ),
    path("runs/<int:pk>/points.csv", runs_views.run_points_csv, name="run-points-csv"),
    path("runs/<int:pk>/pairs.csv", runs_views.run_pairs_csv, name="run-pairs-csv"),
    path("charts/<str:key>/", runs_views.stored_chart, name="stored-chart"),
    path("features/", features_views.features_list, name="features"),
    path("features/new/", features_views.feature_new, name="feature-new"),
    path("features/<int:pk>/", features_views.feature, name="feature"),
]
