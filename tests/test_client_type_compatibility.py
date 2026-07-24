"""Compatibility contracts for the decomposed reusable-client types."""

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    device_types,
    map_renderer_types,
    map_types,
)
from custom_components.dreame_lawn_mower.dreame_lawn_mower_client import (
    types as types_facade,
)

EXPECTED_HISTORICAL_TYPE_EXPORTS = frozenset(
    {
        "SEGMENT_TYPE_CODE_TO_NAME",
        "SEGMENT_TYPE_CODE_TO_HA_ICON",
        "FURNITURE_TYPE_TO_DIMENSIONS",
        "FURNITURE_V2_TYPE_TO_DIMENSIONS",
        "piid",
        "siid",
        "aiid",
        "ATTR_A",
        "ATTR_X",
        "ATTR_X0",
        "ATTR_X1",
        "ATTR_X2",
        "ATTR_X3",
        "ATTR_Y",
        "ATTR_Y0",
        "ATTR_Y1",
        "ATTR_Y2",
        "ATTR_Y3",
        "ATTR_CHARGER",
        "ATTR_IS_EMPTY",
        "ATTR_NO_GO_AREAS",
        "ATTR_PREDEFINED_POINTS",
        "ATTR_VIRTUAL_WALLS",
        "ATTR_PATHWAYS",
        "ATTR_ZONES",
        "ATTR_ROBOT_POSITION",
        "ATTR_MAP_ID",
        "ATTR_MAP_NAME",
        "ATTR_ROTATION",
        "ATTR_TIMESTAMP",
        "ATTR_UPDATED",
        "ATTR_ACTIVE_AREAS",
        "ATTR_ACTIVE_POINTS",
        "ATTR_ACTIVE_CRUISE_POINTS",
        "ATTR_ACTIVE_SEGMENTS",
        "ATTR_FRAME_ID",
        "ATTR_MAP_INDEX",
        "ATTR_ZONE_ID",
        "ATTR_ZONE_ICON",
        "ATTR_UNIQUE_ID",
        "ATTR_FLOOR_MATERIAL",
        "ATTR_FLOOR_MATERIAL_DIRECTION",
        "ATTR_VISIBILITY",
        "ATTR_NAME",
        "ATTR_OUTLINE",
        "ATTR_CENTER",
        "ATTR_ORDER",
        "ATTR_CLEANING_TIMES",
        "ATTR_CLEANING_MODE",
        "ATTR_CLEANING_ROUTE",
        "ATTR_TYPE",
        "ATTR_INDEX",
        "ATTR_ICON",
        "ATTR_COLOR_INDEX",
        "ATTR_OBSTACLES",
        "ATTR_POSSIBILTY",
        "ATTR_PICTURE_STATUS",
        "ATTR_IGNORE_STATUS",
        "ATTR_ZONE",
        "ATTR_ROUTER_POSITION",
        "ATTR_FURNITURES",
        "ATTR_STARTUP_METHOD",
        "ATTR_RECOVERY_MAP_LIST",
        "ATTR_WIDTH",
        "ATTR_HEIGHT",
        "ATTR_SIZE_TYPE",
        "ATTR_ANGLE",
        "ATTR_SCALE",
        "ATTR_COMPLETED",
        "ATTR_FIRMWARE_VERSION",
        "ATTR_AP",
        "ATTR_COLOR_SCHEME",
        "DreameMowerChargingStatus",
        "DreameMowerErrorCode",
        "DreameMowerState",
        "DreameMowerStateOld",
        "DreameMowerCleaningMode",
        "DreameMowerRelocationStatus",
        "DreameMowerTaskStatus",
        "DreameMowerStatus",
        "DreameMowerDustCollection",
        "DreameMowerAutoEmptyStatus",
        "DreameMowerSelfCleanArea",
        "DreameMowerCleaningRoute",
        "DreameMowerWiderCornerCoverage",
        "DreameMowerCleanGenius",
        "DreameMowerSecondCleaning",
        "DreameMowerFloorMaterial",
        "DreameMowerFloorMaterialDirection",
        "DreameMowerSegmentVisibility",
        "DreameMowerVoiceAssistantLanguage",
        "DreameMowerStreamStatus",
        "DreameMowerTaskType",
        "DreameMapRecoveryStatus",
        "DreameMapBackupStatus",
        "DreameMowerProperty",
        "DreameMowerAutoSwitchProperty",
        "DreameMowerStrAIProperty",
        "DreameMowerAIProperty",
        "DreameMowerAction",
        "DreameMowerPropertyMapping",
        "DreameMowerActionMapping",
        "PROPERTY_AVAILABILITY",
        "ACTION_AVAILABILITY",
        "PIID",
        "DIID",
        "RobotType",
        "PathType",
        "ObstacleType",
        "ObstacleIgnoreStatus",
        "ObstaclePictureStatus",
        "SegmentNeglectReason",
        "TaskInterruptReason",
        "FurnitureType",
        "CleansetType",
        "DeviceCapability",
        "DreameMowerDeviceCapability",
        "DirtyData",
        "Shortcut",
        "ShortcutTask",
        "DNDTask",
        "GoToZoneSettings",
        "Point",
        "Path",
        "Obstacle",
        "Zone",
        "Segment",
        "Wall",
        "Area",
        "Furniture",
        "Coordinate",
        "MapImageDimensions",
        "CleaningHistory",
        "RecoveryMapInfo",
        "MapFrameType",
        "MapPixelType",
        "RecoveryMapType",
        "StartupMethod",
        "CleanupMethod",
        "TaskEndType",
        "MapDataPartial",
        "MapData",
        "MapRendererConfig",
        "MapRendererColorScheme",
        "MAP_COLOR_SCHEME_LIST",
        "MAP_ICON_SET_LIST",
        "MapRendererLayer",
        "Line",
        "CLine",
        "ALine",
        "Paths",
        "Angle",
        "MapRendererResources",
        "MapRendererData",
    }
)


def test_types_facade_reexports_every_owned_type() -> None:
    """Historical imports from ``types`` resolve to the new semantic owners."""
    owners = (device_types, map_types, map_renderer_types)
    expected_exports = {name for owner in owners for name in owner.__all__}

    assert expected_exports == EXPECTED_HISTORICAL_TYPE_EXPORTS
    assert expected_exports == set(types_facade.__all__)
    for owner in owners:
        for name in owner.__all__:
            assert getattr(types_facade, name) is getattr(owner, name)


def test_type_owners_have_distinct_public_contracts() -> None:
    """A type has one canonical implementation owner."""
    owners = (device_types, map_types, map_renderer_types)

    for index, owner in enumerate(owners):
        for other in owners[index + 1 :]:
            assert set(owner.__all__).isdisjoint(other.__all__)


def test_representative_historical_type_imports_keep_identity() -> None:
    """Cover the device, map, and renderer import paths used by consumers."""
    assert types_facade.DreameMowerProperty is device_types.DreameMowerProperty
    assert types_facade.DirtyData is device_types.DirtyData
    assert types_facade.MapData is map_types.MapData
    assert types_facade.Segment is map_types.Segment
    assert types_facade.MapRendererConfig is map_renderer_types.MapRendererConfig
    assert types_facade.MapRendererResources is map_renderer_types.MapRendererResources
