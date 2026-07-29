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
