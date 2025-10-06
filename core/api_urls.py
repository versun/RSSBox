from django.urls import path
from . import api_views

app_name = "core_api"
urlpatterns = [
    # Feeds
    path("feeds/", api_views.feeds_list, name="feeds_list"),
    path("feeds/create/", api_views.feed_create, name="feed_create"),
    path("feeds/<int:pk>/", api_views.feed_detail, name="feed_detail"),
    path("feeds/<int:pk>/edit/", api_views.feed_edit, name="feed_edit"),
    path("feeds/<int:pk>/delete/", api_views.feed_delete, name="feed_delete"),
    path("feeds/<int:pk>/entries/", api_views.feed_entries, name="feed_entries"),

    # Tags
    path("tags/", api_views.tags_list, name="tags_list"),
    path("tags/create/", api_views.tag_create, name="tag_create"),
    path("tags/<int:pk>/", api_views.tag_detail, name="tag_detail"),
    path("tags/<int:pk>/edit/", api_views.tag_edit, name="tag_edit"),
    path("tags/<int:pk>/delete/", api_views.tag_delete, name="tag_delete"),

    # Entries
    path("entries/", api_views.entries_list, name="entries_list"),
    path("entries/<int:pk>/", api_views.entry_detail, name="entry_detail"),

    # Digests
    path("digests/", api_views.digests_list, name="digests_list"),
    path("digests/create/", api_views.digest_create, name="digest_create"),
    path("digests/<int:pk>/", api_views.digest_detail, name="digest_detail"),
    path("digests/<int:pk>/edit/", api_views.digest_edit, name="digest_edit"),
    path("digests/<int:pk>/delete/", api_views.digest_delete, name="digest_delete"),

    # Filters
    path("filters/", api_views.filters_list, name="filters_list"),
    path("filters/create/", api_views.filter_create, name="filter_create"),
    path("filters/<int:pk>/", api_views.filter_detail, name="filter_detail"),
    path("filters/<int:pk>/edit/", api_views.filter_edit, name="filter_edit"),
    path("filters/<int:pk>/delete/", api_views.filter_delete, name="filter_delete"),
    path("filters/<int:pk>/test/", api_views.filter_test, name="filter_test"),

    # Agents
    path("agents/", api_views.agents_list, name="agents_list"),
    path("agents/create/", api_views.agent_create, name="agent_create"),
    path("agents/<int:pk>/", api_views.agent_detail, name="agent_detail"),
    path("agents/<int:pk>/edit/", api_views.agent_edit, name="agent_edit"),
    path("agents/<int:pk>/delete/", api_views.agent_delete, name="agent_delete"),
    path("agents/<int:pk>/test/", api_views.agent_test, name="agent_test"),

    # Import OPML
    path("feeds/import-opml/", api_views.import_opml_form, name="import_opml_form"),
    path("feeds/import-opml/process/", api_views.import_opml_process, name="import_opml_process"),

    # Form Processing
    path("tags/create/", api_views.tag_create_process, name="tag_create_process"),
    path("tags/<int:pk>/update/", api_views.tag_update_process, name="tag_update_process"),
    path("digests/create/", api_views.digest_create_process, name="digest_create_process"),
    path("digests/<int:pk>/update/", api_views.digest_update_process, name="digest_update_process"),
    path("filters/create/", api_views.filter_create_process, name="filter_create_process"),
    path("filters/<int:pk>/update/", api_views.filter_update_process, name="filter_update_process"),
    path("agents/create/", api_views.agent_create_process, name="agent_create_process"),
    path("agents/<int:pk>/update/", api_views.agent_update_process, name="agent_update_process"),
]
