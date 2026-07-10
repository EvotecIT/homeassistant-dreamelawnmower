"""Dreame TX video credential derivation."""

from __future__ import annotations

from Crypto.Cipher import AES


# Dreamehome Android 2.5.8.1 supplies this app-level key to TXVideoSdk.
# The authenticated getIdentity response returns an encrypted QCloud pair;
# TXVideoSdk keeps the encrypted values for QCloud and decrypts the same pair
# into the Tencent XP2P application credentials.
_DREAMEHOME_TX_VIDEO_ENCRYPTION_KEY = "EETjszu*XI5znHsI"


def derive_tx_video_app_credentials(
    secret_id: str | None,
    secret_key: str | None,
) -> tuple[str | None, str | None]:
    """Derive Tencent XP2P app credentials from Dreame identity values."""
    return (
        decrypt_tx_video_app_credential(secret_id),
        decrypt_tx_video_app_credential(secret_key),
    )


def decrypt_tx_video_app_credential(value: str | None) -> str | None:
    """Mirror Dreamehome's DMDecryptor AES-128-CBC identity decoding."""
    if not value:
        return None

    ciphertext_hex = value.strip()
    if ciphertext_hex.casefold().startswith("0x"):
        ciphertext_hex = ciphertext_hex[2:]

    try:
        ciphertext = bytes.fromhex(ciphertext_hex)
    except ValueError:
        return None
    if not ciphertext or len(ciphertext) % AES.block_size:
        return None

    key = _DREAMEHOME_TX_VIDEO_ENCRYPTION_KEY.encode("utf-8")
    if len(key) != AES.block_size:
        return None

    try:
        plaintext = AES.new(key, AES.MODE_CBC, iv=key).decrypt(ciphertext)
        decoded = plaintext.rstrip(b"\x00").decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None

    credential = decoded.split("|", 1)[0]
    return credential or None
