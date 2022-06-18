"""Collections for User Profiles.

These classes shouldn't be created manually.
Use the helper methods such as:
:meth:`pyremoteplay.profile.Profiles.load() <pyremoteplay.profile.Profiles.load>`
and
:meth:`pyremoteplay.device.RPDevice.get_profiles() <pyremoteplay.device.RPDevice.get_profiles>`

"""
from __future__ import annotations
from collections import UserDict
from typing import Union
import logging

from pyremoteplay.oauth import get_user_account
from .util import get_profiles, write_profiles, get_users, add_regist_data

_LOGGER = logging.getLogger(__name__)


def format_user_account(user_data: dict) -> UserProfile:
    """Format account data to user profile. Return user profile.

    :param user_data: User data. \
        See :meth:`pyremoteplay.oauth.get_user_account() <pyremoteplay.oauth.get_user_account>`
    """
    user_id = user_data.get("user_rpid")
    if not isinstance(user_id, str) and not user_id:
        _LOGGER.error("Invalid user id or user id not found")
        return None
    name = user_data["online_id"]
    data = {
        "id": user_id,
        "hosts": {},
    }
    return UserProfile(name, data)


class HostProfile(UserDict):
    """Host Profile for User."""

    def __init__(self, name: str, data: dict):
        if not name or not isinstance(name, str):
            raise ValueError("Name must be a non-blank string")
        self.__name = name
        self.__type = data["type"]
        super().__init__(data["data"])
        self._verify()

    def _verify(self):
        assert self.name, "Attribute 'name' cannot be empty"
        assert self.regist_key, "Attribute 'regist_key' cannot be empty"
        assert self.rp_key, "Attribute 'rp_key' cannot be empty"

    @property
    def name(self) -> str:
        """Return Name / Mac Address."""
        return self.__name

    @property
    def type(self) -> str:
        """Return type."""
        return self.__type

    @property
    def regist_key(self) -> str:
        """Return Regist Key."""
        return self.data["RegistKey"]

    @property
    def rp_key(self) -> str:
        """Return RP Key."""
        return self.data["RP-Key"]


class UserProfile(UserDict):
    """PSN User Profile. Stores Host Profiles for user."""

    def __init__(self, name: str, data: dict):
        if not name or not isinstance(name, str):
            raise ValueError("Name must be a non-blank string")
        self.__name = name
        super().__init__(data)
        self._verify()

    def _verify(self):
        assert self.name, "Attribute 'name' cannot be empty"
        assert self.id, "Attribute 'id' cannot be empty"

    def update_host(self, host_profile: HostProfile):
        """Update host profile.

        :param: host_profile: Host Profile
        """
        if not isinstance(host_profile, HostProfile):
            raise ValueError(
                f"Expected instance of {HostProfile}. Got {type(host_profile)}"
            )
        # pylint: disable=protected-access
        host_profile._verify()
        self[host_profile.name] = host_profile.data

    def add_regist_data(self, host_status: dict, data: dict):
        """Add regist data to user profile.

        :param host_status: Status from device. \
            See :meth:`pyremoteplay.device.RPDevice.get_status() \
                <pyremoteplay.device.RPDevice.get_status>`
        :param data: Data from registering. \
            See :func:`pyremoteplay.register.register() <pyremoteplay.register.register>`
        """
        add_regist_data(self.data, host_status, data)

    @property
    def name(self) -> str:
        """Return PSN Username."""
        return self.__name

    # pylint: disable=invalid-name
    @property
    def id(self) -> str:
        """Return Base64 encoded User ID."""
        return self.data["id"]

    @property
    def hosts(self) -> list[HostProfile]:
        """Return Host profiles."""
        hosts = self.data.get("hosts")
        if not hosts:
            return []
        return [HostProfile(name, data) for name, data in hosts.items()]


class Profiles(UserDict):
    """Collection of User Profiles."""

    __DEFAULT_PATH: str = ""

    @classmethod
    def set_default_path(cls, path: str):
        """Set default path for loading and saving.

        :param path: Path to file.
        """
        cls.__DEFAULT_PATH = path

    @classmethod
    def default_path(cls) -> str:
        """Return default path."""
        return cls.__DEFAULT_PATH

    @classmethod
    def load(cls, path: str = "") -> Profiles:
        """Load profiles from file.

        :param path: Path to file.
            If not given will use \
                :meth:`default_path() <pyremoteplay.profile.Profiles.default_path>`.
            File will be created automatically if it does not exist.
        """
        path = cls.__DEFAULT_PATH if not path else path
        return cls(get_profiles(path))

    def new_user(self, redirect_url: str, save=True) -> UserProfile:
        """Create New PSN user.

        See :func:`pyremoteplay.oauth.get_login_url() <pyremoteplay.oauth.get_login_url>`.

        :param redirect_url: URL from signing in with PSN account at the login url
        :param save: Save profiles to file if True
        """
        account_data = get_user_account(redirect_url)
        if not account_data:
            return None
        profile = format_user_account(account_data)
        if not profile:
            return None
        self.update_user(profile)
        if save:
            self.save()
        return profile

    def update_user(self, user_profile: UserProfile):
        """Update stored User Profile.

        :param user_profile: User Profile
        """
        if not isinstance(user_profile, UserProfile):
            raise ValueError(
                f"Expected instance of {UserProfile}. Got {type(user_profile)}"
            )
        # pylint: disable=protected-access
        user_profile._verify()
        self[user_profile.name] = user_profile.data

    def update_host(self, user_profile: UserProfile, host_profile: HostProfile):
        """Update host in User Profile.

        :param user_profile: User Profile
        :param host_profile: Host Profile
        """
        user_profile.update_host(host_profile)
        self.update_user(user_profile)

    def remove_user(self, user: Union[str, UserProfile]):
        """Remove user.

        :param user: User profile or user name to remove
        """
        if isinstance(user, UserProfile):
            user = user.name
        if user in self.data:
            self.data.pop(user)

    def save(self, path: str = ""):
        """Save profiles to file.

        :param path: Path to file. If not given will use default path.
        """
        write_profiles(self.data, path)

    def get_users(self, device_id: str) -> list[str]:
        """Return all users that are registered with a device.

        :param device_id: Device ID / Device Mac Address
        """
        return get_users(device_id, self)

    def get_user_profile(self, user: str) -> UserProfile:
        """Return User Profile for user.

        :param user: PSN ID / Username
        """
        profile = None
        for _profile in self.users:
            if _profile.name == user:
                profile = _profile
                break
        return profile

    @property
    def usernames(self) -> list[str]:
        """Return list of user names."""
        return [name for name in self.data]

    @property
    def users(self) -> list[UserProfile]:
        """Return User Profiles."""
        return [UserProfile(name, data) for name, data in self.data.items()]
