"""External-device client for C.O.R.E. (stdlib only, no core imports)."""

from .core_device_client import CoreDeviceClient, DeviceClientError, generate_join_name

__all__ = ["CoreDeviceClient", "DeviceClientError", "generate_join_name"]
