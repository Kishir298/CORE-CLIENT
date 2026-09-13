"""External-device client for C.O.R.E. (stdlib only, no core imports)."""

from .core_device_client import CoreDeviceClient, DeviceClientError

__all__ = ["CoreDeviceClient", "DeviceClientError"]
