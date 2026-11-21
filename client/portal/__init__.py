"""Client portal package (presentation/control interface only).

Reads the live :class:`CoreDeviceClient` through its public API plus local
capability/geo probes. Never handles the provisioning credential, never
renders secrets, never persists the session token anywhere.
"""

from .models import envelope, redact

__all__ = ["envelope", "redact"]
