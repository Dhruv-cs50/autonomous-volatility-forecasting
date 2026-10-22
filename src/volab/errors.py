"""Exception taxonomy for the volab data layer.

Every exception raised by volab derives from VolabError, so callers can catch
everything from the data layer with one clause, while tests can assert on the
specific failure rather than on a message string.
"""


class VolabError(Exception):
    """Base class for every exception raised by volab."""


class HoldoutViolation(VolabError):
    """Raised on any attempted or detected read past dev_end without both keys.

    This includes the post-read assertion.
    """


class ConfigError(VolabError):
    """Raised when configuration is missing, unparseable, or internally inconsistent."""


class DatasetNotFound(VolabError):
    """Raised when the requested tier/dataset pair, or a requested symbol within it,
    matches no files.

    The data is missing; contrast UnknownSymbol, where the request was wrong.
    """


class UnknownSymbol(VolabError):
    """Raised when a requested symbol is absent from settings.universe.

    The request was wrong; contrast DatasetNotFound, where the data is missing.
    """


class SchemaError(VolabError):
    """Raised when a file lacks a mandatory column, or a requested one, or holds rows the
    store cannot tell apart.

    One dtype is checked, and only one: ``ts`` must be timezone-aware, because a naive
    timestamp cannot be placed on a timeline. ``as_of`` and ``ingested_at`` are not dtype-
    checked here; a bad ``as_of`` fails closed on the partition-consistency assertion instead.
    """


class RawOverwrite(VolabError):
    """Raised when a write would replace an existing raw Parquet file.

    Raw files are immutable: they are the only copy of a vendor response whose source window
    may since have moved on, so the write path refuses to replace one rather than clobbering it.
    The expected cause is two ingests of the same source, symbol and date inside one second,
    because the raw filename stamp has whole-second precision. Curated and feature files are
    deterministic derivations and are overwritten instead, which is what makes a rebuild
    idempotent.
    """


class ParseRefused(VolabError):
    """Raised when a vendor response arrived and this code could not read it.

    The expected cause is an HTML maintenance page where a CSV was due, a truncated body, or a
    payload whose shape has changed. The response arrived and was rejected *before* anything was
    archived, so the archive is intact and nothing was written. Retrying now will refuse again;
    look at what the vendor served. Contrast a fetch that did not complete (a timeout, a
    connection error, a non-200 status), which says nothing about the payload and is worth
    retrying later.

    Also a ``ValueError``, because ``fetch_source`` raised a bare one for an unparseable body
    before this type existed, and a caller catching that must keep working. The exception that
    the parser actually raised is chained as ``__cause__``.
    """
