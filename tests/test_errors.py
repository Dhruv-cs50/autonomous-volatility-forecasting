import pytest

from volab.errors import (
    ConfigError,
    DatasetNotFound,
    HoldoutViolation,
    SchemaError,
    UnknownSymbol,
    VolabError,
)


@pytest.mark.parametrize(
    "exc",
    [HoldoutViolation, ConfigError, DatasetNotFound, UnknownSymbol, SchemaError],
)
def test_every_error_derives_from_volab_error(exc: type[Exception]) -> None:
    assert issubclass(exc, VolabError)


def test_volab_error_derives_from_exception() -> None:
    assert issubclass(VolabError, Exception)
