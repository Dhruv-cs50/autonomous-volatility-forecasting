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
    """Raised when a file lacks a mandatory column or has the wrong type for one."""
