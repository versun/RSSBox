from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    # path("filter/<str:name>", views.filter, name="filter"),
    path(
        "tag/proxy/<str:tag>",
        views.tag,
        kwargs={"feed_type": "o", "format": "xml"},
    ),
    path(
        "tag/proxy/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "o", "format": "xml"},
    ),
    path(
        "tag/json/<str:tag>",
        views.tag,
        kwargs={"feed_type": "t", "format": "json"},
    ),
    path(
        "tag/json/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "t", "format": "json"},
    ),
    path(
        "tag/<str:tag>",
        views.tag,
        kwargs={"feed_type": "t", "format": "xml"},
    ),
    path(
        "tag/<str:tag>/",
        views.tag,
        kwargs={"feed_type": "t", "format": "xml"},
    ),
    path(
        "proxy/<str:feed_slug>", views.rss, kwargs={"feed_type": "o", "format": "xml"}
    ),
    path(
        "proxy/<str:feed_slug>/", views.rss, kwargs={"feed_type": "o", "format": "xml"}
    ),
    path(
        "json/<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "format": "json"}
    ),
    path(
        "json/<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "format": "json"}
    ),
    path("import_opml/", views.import_opml, name="import_opml"),
    path("<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
    path("<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
]
