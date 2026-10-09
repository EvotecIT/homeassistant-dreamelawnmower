"""Cloud-reported identity details for the legacy mower device."""

from __future__ import annotations

from collections.abc import Mapping as _Mapping


class DreameMowerDeviceInfo:
    """Container of device information."""

    def __init__(self, data: _Mapping[str, object]) -> None:
        self.data = data
        self.version = 0
        firmware_version = self.firmware_version
        if firmware_version is not None:
            firmware_parts = firmware_version.split("_")
            if len(firmware_parts) == 2:
                try:
                    self.version = int(firmware_parts[1])
                except ValueError:
                    pass

    def __repr__(self) -> str:
        network = self.network_interface
        local_ip = network.get("localIp", "") if network else ""
        return f"{self.model} v{self.version} ({self.mac_address}) @ {local_ip}"

    @property
    def network_interface(self) -> _Mapping[str, object] | None:
        """Information about network configuration."""
        value = self.data.get("netif")
        return value if isinstance(value, _Mapping) else None

    @property
    def model(self) -> str | None:
        """Model string if available."""
        value = self.data.get("model")
        return value if isinstance(value, str) else None

    @property
    def firmware_version(self) -> str | None:
        """Firmware version if available."""
        for key in ("fw_ver", "ver"):
            value = self.data.get(key)
            if isinstance(value, str):
                return value
        return None

    @property
    def hardware_version(self) -> str | None:
        """Hardware version if available."""
        value = self.data.get("hw_ver", "Linux")
        return value if isinstance(value, str) else None

    @property
    def mac_address(self) -> str | None:
        """MAC address if available."""
        value = self.data.get("mac")
        return value if isinstance(value, str) else None

    @property
    def manufacturer(self) -> str:
        """Manufacturer name."""
        return "Dreametech™"

    @property
    def raw(self) -> _Mapping[str, object]:
        """Raw data as returned by the device."""
        return self.data
