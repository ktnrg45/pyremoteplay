"""Errors for pyremoteplay."""
from enum import Enum, IntEnum


class RPErrorHandler():
    """Remote Play Errors."""

    def __init__(self):
        pass

    def __call__(self, error: int) -> str:
        try:
            error = RPErrorHandler.Type(error)
        except ValueError:
            return f"Unknown Error type: {error}"
        error = RPErrorHandler.Message[error.name].value
        return error

    class Type(IntEnum):
        """Enum for errors."""
        REGIST_FAILED = 0x80108b09
        INVALID_PSN_ID = 0x80108b02
        RP_IN_USE = 0x80108b10
        CRASH = 0x80108b15
        RP_VERSION_MISMATCH = 0x80108b11
        UNKNOWN = 0x80108bff

    class Message(Enum):
        """Messages for Error."""
        REGIST_FAILED = "Registering Failed"
        INVALID_PSN_ID = "PSN ID does not exist on host"
        RP_IN_USE = "Another Remote Play session is connected to host"
        CRASH = "RP Crashed on Host; Host needs restart"
        RP_VERSION_MISMATCH = "Remote Play versions do not match on host and client"
        UNKNOWN = "Unknown"


class RemotePlayError(Exception):
    """General Remote Play Exception."""


class CryptError(Exception):
    """General Crypt Exception."""
