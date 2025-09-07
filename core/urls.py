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
    # Digest URLs
    path("digest/view/<str:slug>", views.digest_view, name="digest_view"),
    path("digest/view/<str:slug>/", views.digest_view, name="digest_view"),
    path("digest/json/<str:slug>", views.digest, kwargs={"format": "json"}, name="digest_json"),
    path("digest/json/<str:slug>/", views.digest, kwargs={"format": "json"}, name="digest_json"),
    path("digest/<str:slug>", views.digest, kwargs={"format": "xml"}, name="digest_rss"),
    path("digest/<str:slug>/", views.digest, kwargs={"format": "xml"}, name="digest_rss"),
    path("<str:feed_slug>", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
    path("<str:feed_slug>/", views.rss, kwargs={"feed_type": "t", "format": "xml"}),
]
