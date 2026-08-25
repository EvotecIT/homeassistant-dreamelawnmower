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


_WRITE_ATTEMPTED_ATTRIBUTE = "_dreame_write_attempted"


def mark_write_attempted(error: Exception) -> None:
    """Record that a state-changing request may have reached the mower."""
    setattr(error, _WRITE_ATTEMPTED_ATTRIBUTE, True)


def write_was_attempted(error: BaseException) -> bool:
    """Return whether an error followed a state-changing request attempt."""
    return getattr(error, _WRITE_ATTEMPTED_ATTRIBUTE, False) is True


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
