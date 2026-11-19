"""Unicode-aware case-insensitive "contains" for keyword filters.

Django's ``icontains`` becomes ``LIKE ... ESCAPE`` on SQLite, and SQLite's
LIKE only folds ASCII letters. A keyword like "готвач" therefore missed
"Готвач" and "ГОТВАЧ" (and Tagulous stores "готвач"/"ГОТВАЧ" as one tag, so
both spellings cannot be listed). ``uicontains`` compares
``str.casefold()`` of both sides in Python on SQLite, and falls back to the
database's own ``icontains`` elsewhere (PostgreSQL/MySQL fold Unicode).
"""

from django.db.backends.signals import connection_created
from django.db.models import CharField, Lookup, TextField
from django.db.models.lookups import IContains

SQLITE_FUNCTION = "rssbox_ucontains"


def _ucontains(haystack, needle):
    if haystack is None or needle is None:
        return 0
    return 1 if str(needle).casefold() in str(haystack).casefold() else 0


def register_sqlite_function(connection):
    if connection.vendor == "sqlite" and connection.connection is not None:
        connection.connection.create_function(
            SQLITE_FUNCTION, 2, _ucontains, deterministic=True
        )


def _on_connection_created(sender, connection, **kwargs):
    register_sqlite_function(connection)


class UnicodeIContains(Lookup):
    lookup_name = "uicontains"

    def as_sql(self, compiler, connection):
        if connection.vendor != "sqlite":
            return IContains(self.lhs, self.rhs).as_sql(compiler, connection)
        lhs, lhs_params = self.process_lhs(compiler, connection)
        rhs, rhs_params = self.process_rhs(compiler, connection)
        return f"{SQLITE_FUNCTION}({lhs}, {rhs})", (*lhs_params, *rhs_params)


def install():
    """Register the lookup and the SQLite function (called from AppConfig.ready)."""
    CharField.register_lookup(UnicodeIContains)
    TextField.register_lookup(UnicodeIContains)
    connection_created.connect(_on_connection_created, dispatch_uid="rssbox_ucontains")
    # Connections opened before ready() (rare) get the function too.
    from django.db import connections

    for conn in connections.all(initialized_only=True):
        register_sqlite_function(conn)
