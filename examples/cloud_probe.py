"""Example for probing Dreamehome cloud endpoints used by the mobile app."""

from __future__ import annotations

import asyncio
import json
import os

from dreame_lawn_mower_client import DreameLawnMowerClient


def _property_keys_from_env() -> list[str]:
    raw = os.environ.get("DREAME_PROP_KEYS", "")
    return [item.strip() for item in raw.split(",") if item.strip()]


async def main() -> None:
    username = os.environ["DREAME_USERNAME"]
    password = os.environ["DREAME_PASSWORD"]
    country = os.environ.get("DREAME_COUNTRY", "eu")
    account_type = os.environ.get("DREAME_ACCOUNT_TYPE", "dreame")

    devices = await DreameLawnMowerClient.async_discover_devices(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
    )
    if not devices:
        raise RuntimeError("No mower devices found.")

    client = DreameLawnMowerClient(
        username=username,
        password=password,
        country=country,
        account_type=account_type,
        descriptor=devices[0],
    )
    try:
        print("Descriptor:", devices[0].title)

        device_info = await client.async_get_cloud_device_info()
        print("device/info:")
        print(json.dumps(device_info, indent=2, sort_keys=True))

        user_features = await client.async_get_cloud_user_features()
        print("queryDevicePermit:")
        print(json.dumps(user_features, indent=2, sort_keys=True))

        otc_info = await client.async_get_cloud_device_otc_info()
        print("devOTCInfo:")
        print(json.dumps(otc_info, indent=2, sort_keys=True))

        device_page = await client.async_get_cloud_device_list_page()
        print("device/listV2 summary:")
        print(
            json.dumps(
                {
                    "current": device_page.get("current") if device_page else None,
                    "size": device_page.get("size") if device_page else None,
                    "total": device_page.get("total") if device_page else None,
                    "record_count": len(device_page.get("records", []))
                    if device_page
                    else None,
                },
                indent=2,
                sort_keys=True,
            )
        )

        property_keys = _property_keys_from_env()
        if property_keys:
            properties = await client.async_get_cloud_properties(property_keys)
            print("iotstatus/props:")
            print(json.dumps(properties, indent=2, sort_keys=True))
    finally:
        await client.async_close()


if __name__ == "__main__":
    asyncio.run(main())
