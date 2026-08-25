from collections.abc import Sequence


class DeviceException(Exception):
    """Exception wrapping any communication errors with the device."""


class DeviceUpdateFailedException(DeviceException):
    """ """


class DeviceCommandRejectedException(DeviceUpdateFailedException):
    """The mower explicitly rejected a state-changing device command."""


class InvalidValueException(ValueError):
    """ """


class InvalidActionException(ValueError):
    """ """


class DreameLawnMowerError(Exception):
    """Base reusable client exception."""


class DreameLawnMowerAuthError(DreameLawnMowerError):
    """Login or credential failure."""


class DreameLawnMowerConnectionError(DreameLawnMowerError):
    """Connection or update failure."""


class DreameLawnMowerCommandRejectedError(DreameLawnMowerConnectionError):
    """The mower explicitly rejected a state-changing app command."""


_WRITE_ATTEMPTED_FIELDS_ATTRIBUTE = "_dreame_write_attempted_fields"


def mark_write_attempted(error: Exception, *, fields: Sequence[str]) -> None:
    """Record that a state-changing request may have reached the mower."""
    attempted_fields = dict.fromkeys((*attempted_write_fields(error), *fields))
    setattr(error, _WRITE_ATTEMPTED_FIELDS_ATTRIBUTE, tuple(attempted_fields))


def attempted_write_fields(error: BaseException) -> tuple[str, ...]:
    """Return fields whose state-changing requests may have reached the mower."""
    fields = getattr(error, _WRITE_ATTEMPTED_FIELDS_ATTRIBUTE, ())
    return (
        tuple(field for field in fields if isinstance(field, str))
        if isinstance(fields, Sequence) and not isinstance(fields, str | bytes)
        else ()
    )


class DreameLawnMowerCloudAPIError(DeviceException):
    """A privacy-safe numeric error returned by the Dreame cloud API."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Dreame cloud API request failed with code {code}.")
        self.code = code


class DreameLawnMowerTwoFactorRequiredError(DreameLawnMowerAuthError):
    """Two-factor authentication is required."""

    def __init__(self, url: str) -> None:
        super().__init__("Two-factor authentication is required.")
        self.url = url
