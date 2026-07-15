"""TLS configuration for Dreame and MOVA cloud MQTT brokers."""

from __future__ import annotations

import ssl
from importlib.resources import files

_VENDOR_ROOT_CA = ("certs", "dreame_mqtt_root_ca.pem")


def create_cloud_mqtt_ssl_context() -> ssl.SSLContext:
    """Return a verified TLS context that also trusts Dreame's private CA.

    Some Dreame and MOVA MQTT brokers use a private certificate hierarchy whose
    subjects resemble GlobalSign certificates but whose root is not publicly
    trusted. The private root is added only to this MQTT client context; the
    process-wide and system CA stores are left unchanged.
    """

    context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
    # The vendor leaf omits Authority Key Identifier, which OpenSSL rejects in
    # strict RFC 5280 mode. Chain and hostname verification remain enabled.
    context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    vendor_root = files(__package__).joinpath(*_VENDOR_ROOT_CA).read_text(
        encoding="ascii"
    )
    context.load_verify_locations(cadata=vendor_root)
    return context
