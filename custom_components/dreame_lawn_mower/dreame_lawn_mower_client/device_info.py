"""Cloud-reported identity details for the legacy mower device."""

from __future__ import annotations

import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class DreameMowerDeviceInfo:
    """Container of device information."""

    def __init__(self, data):
        self.data = data
        self.version = 0
        firmware_version = self.firmware_version
        if firmware_version is not None:
            firmware_version = firmware_version.split("_")
            if len(firmware_version) == 2:
                self.version = int(firmware_version[1])

    def __repr__(self):
        local_ip = (
            self.network_interface.get("localIp", "") if self.network_interface else ""
        )
        return f"{self.model} v{self.version} ({self.mac_address}) @ {local_ip}"

    @property
    def network_interface(self) -> str:
        """Information about network configuration."""
        if "netif" in self.data:
            return self.data["netif"]
        return None

    @property
    def model(self) -> str | None:
        """Model string if available."""
        if "model" in self.data:
            return self.data["model"]
        return None

    @property
    def firmware_version(self) -> str | None:
        """Firmware version if available."""
        if "fw_ver" in self.data and self.data["fw_ver"] is not None:
            return self.data["fw_ver"]
        if "ver" in self.data and self.data["ver"] is not None:
            return self.data["ver"]
        return None

    @property
    def hardware_version(self) -> str | None:
        """Hardware version if available."""
        if "hw_ver" in self.data:
            return self.data["hw_ver"]
        return "Linux"

    @property
    def mac_address(self) -> str | None:
        """MAC address if available."""
        if "mac" in self.data:
            return self.data["mac"]
        return None

    @property
    def manufacturer(self) -> str:
        """Manufacturer name."""
        return "Dreametech™"

    @property
    def raw(self) -> dict[str, Any]:
        """Raw data as returned by the device."""
        return self.data
