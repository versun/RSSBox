from django.urls import path
from . import frontend_views

app_name = "core_frontend"
urlpatterns = [
    path("", frontend_views.dashboard, name="dashboard"),
    path("feeds/", frontend_views.feeds, name="feeds"),
    path("entries/", frontend_views.entries, name="entries"),
    path("tags/", frontend_views.tags, name="tags"),
    path("digests/", frontend_views.digests, name="digests"),
    path("filters/", frontend_views.filters, name="filters"),
    path("agents/", frontend_views.agents, name="agents"),
]
