"""Shared point-cloud freshness and generation decisions, independent of I/O."""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any

from requests.exceptions import Timeout as RequestsTimeout

from .client_map_helpers import (
    _app_object_extension,
    _point_cloud_object_name,
    _PointCloudObjectIdentity,
    _validate_point_cloud_map_index,
    _validate_positive_number,
)
from .exceptions import DeviceException
from .point_cloud import (
    DreameLawnMowerPointCloudDownload,
    DreameLawnMowerPointCloudError,
)
from .point_cloud_diagnostics import indexed_object_observation, indexed_poll_result
from .point_cloud_policy import (
    _POINT_CLOUD_ACKNOWLEDGED_FIXED_OBJECT_MODELS,
    _POINT_CLOUD_ANNOUNCEMENT_INITIAL_BUDGET_FRACTION,
    _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS,
    _POINT_CLOUD_ANNOUNCEMENT_REPROBE_ATTEMPTS,
    _POINT_CLOUD_ANNOUNCEMENT_REPROBE_TIMEOUT_SECONDS,
    _POINT_CLOUD_ANNOUNCEMENT_RETRY_MAX_SECONDS,
    _POINT_CLOUD_CLOUD_SETUP_TIMEOUT_SECONDS,
    _POINT_CLOUD_LEGACY_BASELINE_TIMEOUT_SECONDS,
    _POINT_CLOUD_LEGACY_POLL_TIMEOUT_SECONDS,
    _POINT_CLOUD_OBJECT_EXTENSIONS,
)
from .point_cloud_trace import record_point_cloud_stage
from .point_cloud_transport import (
    CachedObjectNames,
    CloudSetup,
    DownloadObject,
    MowerAction,
    ObjectIdentity,
    ParseMetadata,
    PointCloudRequest,
    ReadAnnouncement,
    StoredObject,
    WaitForObject,
)


def point_cloud_generation(
    account_type: str,
    model: str,
    map_index: int,
    timeout: float,
    poll_interval: float,
    download_timeout: float,
    max_bytes: int,
    deadline: float | None = None,
    allow_stored: bool = False,
    allow_unscoped_stored: bool | None = None,
) -> Generator[PointCloudRequest, Any, DreameLawnMowerPointCloudDownload]:
    if allow_unscoped_stored is None:
        allow_unscoped_stored = allow_stored
    else:
        allow_unscoped_stored = allow_stored and allow_unscoped_stored
    map_index = _validate_point_cloud_map_index(map_index)
    timeout = _validate_positive_number(timeout, "generation timeout")
    poll_interval = _validate_positive_number(poll_interval, "poll interval")
    download_timeout = _validate_positive_number(
        download_timeout,
        "download timeout",
    )
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud maximum size must be a positive integer.",
            code="point_cloud_invalid_request",
            stage="request",
            retryable=False,
            public_message="The 3D map request contains an invalid size limit.",
        )

    deadline = time.monotonic() + timeout if deadline is None else deadline
    request_deadline = deadline
    if time.monotonic() >= deadline:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud generation timed out.",
            code="point_cloud_timeout",
            stage="generation",
            public_message=(
                f"The mower did not finish the 3D map request within "
                f"{timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
        )
    record_point_cloud_stage("cloud_setup")
    try:
        yield CloudSetup(
            deadline=min(
                deadline,
                time.monotonic() + _POINT_CLOUD_CLOUD_SETUP_TIMEOUT_SECONDS,
            )
        )
    except RequestsTimeout as err:
        raise DreameLawnMowerPointCloudError(
            "Point-cloud cloud setup timed out.",
            code="point_cloud_timeout",
            stage="cloud",
            public_message=(
                "The mower cloud connection timed out while preparing the "
                "3D map request."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
        ) from err
    stored_attempt: dict[str, Any] = {
        "download_attempts": 0,
        "last_download_step": "not_attempted",
    }
    cached_names = yield CachedObjectNames()
    cached_name = _point_cloud_object_name({"name": cached_names}, map_index)
    stored: DreameLawnMowerPointCloudDownload | None
    if allow_stored and cached_name is not None:
        stored = yield StoredObject(
            object_name=cached_name,
            map_index=map_index,
            deadline=deadline,
            download_timeout=download_timeout,
            max_bytes=max_bytes,
            observation=stored_attempt,
        )
        if stored is not None:
            return stored

    remaining = max(0.0, deadline - time.monotonic())
    initial_probe_budget = min(
        _POINT_CLOUD_ANNOUNCEMENT_PROBE_TIMEOUT_SECONDS,
        max(
            0.005,
            timeout * _POINT_CLOUD_ANNOUNCEMENT_INITIAL_BUDGET_FRACTION,
        ),
        remaining,
    )
    initial_announcement: dict[str, Any] = {}
    (
        announcement_capability,
        stored_name,
        announcement_baseline,
    ) = yield ReadAnnouncement(
        requested_after_ms=0,
        fallback_reserve_seconds=max(
            0.0,
            remaining - initial_probe_budget,
        ),
        deadline=deadline,
        observation=initial_announcement,
    )
    use_announcement_path = announcement_capability is True
    announcement_probe_pending = announcement_capability is None
    initial_announcement_capability = (
        "available"
        if announcement_capability is True
        else "unavailable"
        if announcement_capability is False
        else "inconclusive"
    )
    attempt_diagnostics: dict[str, Any] = {
        "map_index": map_index,
        "initial_announcement": initial_announcement,
        "stored_attempt": stored_attempt,
        "generation_result": "not_attempted",
        "last_download_step": "not_attempted",
        "announcement_capability_initial": initial_announcement_capability,
        "announcement_capability": initial_announcement_capability,
        "announcement_baseline": (
            "observed" if announcement_baseline is not None else "not_observed"
        ),
        "announcement_polls": 0,
        "announcement_fresh_observations": 0,
        "announcement_stale_observations": 0,
        "announcement_empty_observations": 0,
        "announcement_unavailable_observations": 0,
        "announcement_inconclusive_observations": 0,
        "indexed_verification_attempts": 0,
        "indexed_verification_result": "not_attempted",
        "download_attempts": 0,
        "last_download_result": "not_attempted",
    }
    record_point_cloud_stage("poll_result", attempt_diagnostics)
    if allow_unscoped_stored and stored_name is not None and stored_name != cached_name:
        stored = yield StoredObject(
            object_name=stored_name,
            map_index=map_index,
            deadline=deadline,
            download_timeout=download_timeout,
            max_bytes=max_bytes,
            observation=stored_attempt,
        )
        if stored is not None:
            return stored
    if allow_stored and use_announcement_path:
        # Some A2 firmware retains an expired 99.20 announcement while
        # OBJ still identifies the PCD for each map. Besides supporting a
        # stored-map fallback, this bounded read disambiguates firmware
        # that refreshes a stable 99.20 key without changing its property
        # timestamp.
        legacy_deadline = min(
            deadline,
            time.monotonic() + _POINT_CLOUD_LEGACY_POLL_TIMEOUT_SECONDS,
        )
        try:
            legacy_result = yield MowerAction(
                payload={"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                operation="read the stored point-cloud object state",
                deadline=legacy_deadline,
                require_data=True,
            )
        except DreameLawnMowerPointCloudError as err:
            attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
            legacy_result = None
        attempt_diagnostics["baseline_indexed"] = indexed_object_observation(
            legacy_result,
            map_index,
        )
        legacy_name = _point_cloud_object_name(
            legacy_result,
            map_index,
        )
        if legacy_name is not None:
            stored = yield StoredObject(
                object_name=legacy_name,
                map_index=map_index,
                deadline=deadline,
                download_timeout=download_timeout,
                max_bytes=max_bytes,
                observation=stored_attempt,
            )
            if stored is not None:
                return stored
    baseline_name = None
    baseline_known = use_announcement_path
    if not use_announcement_path:
        baseline_timeout = (
            _POINT_CLOUD_LEGACY_POLL_TIMEOUT_SECONDS
            if announcement_probe_pending
            else _POINT_CLOUD_LEGACY_BASELINE_TIMEOUT_SECONDS
        )
        baseline_deadline = min(
            deadline,
            time.monotonic() + baseline_timeout,
        )
        try:
            baseline_result = yield MowerAction(
                payload={"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                operation="read the existing point-cloud object state",
                deadline=baseline_deadline,
                require_data=True,
            )
        except DreameLawnMowerPointCloudError as err:
            attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
            err.diagnostic_context.update(attempt_diagnostics)
            if err.code not in {
                "point_cloud_timeout",
                "point_cloud_mower_request_failed",
            }:
                raise
            baseline_result = None
        else:
            baseline_known = (
                indexed_poll_result(
                    indexed_object_observation(baseline_result, map_index),
                )
                == "observed"
            )
        attempt_diagnostics["baseline_indexed"] = indexed_object_observation(
            baseline_result,
            map_index,
        )
        baseline_name = _point_cloud_object_name(
            baseline_result,
            map_index,
        )
        if allow_stored and baseline_name is not None and baseline_name != cached_name:
            stored = yield StoredObject(
                object_name=baseline_name,
                map_index=map_index,
                deadline=deadline,
                download_timeout=download_timeout,
                max_bytes=max_bytes,
                observation=stored_attempt,
            )
            if stored is not None:
                return stored

    accepted_extensions = _POINT_CLOUD_OBJECT_EXTENSIONS
    baseline_identity = None
    baseline_extension = (
        _app_object_extension(baseline_name) if baseline_name is not None else None
    )
    fixed_object_baseline = (
        baseline_name is not None
        and baseline_extension is not None
        and baseline_extension.casefold() == "bin"
    )
    fixed_baseline_known = False
    if fixed_object_baseline and baseline_name is not None:
        fixed_baseline_known, baseline_identity = yield ObjectIdentity(
            object_name=baseline_name,
            deadline=deadline,
            download_timeout=download_timeout,
            max_bytes=max_bytes,
        )

    stable_announcement_baseline_known = False
    stable_announcement_baseline_identity: _PointCloudObjectIdentity | None = None
    if use_announcement_path and announcement_baseline is not None:
        (
            stable_announcement_baseline_known,
            stable_announcement_baseline_identity,
        ) = yield ObjectIdentity(
            object_name=announcement_baseline[0],
            deadline=deadline,
            download_timeout=download_timeout,
            max_bytes=max_bytes,
        )

    # Object baselines are preflight work for both forced and stored
    # requests. Give o:10 the complete advertised generation window after
    # those bounded reads finish.
    deadline = min(request_deadline, time.monotonic() + timeout)
    attempt_diagnostics["fixed_baseline"] = (
        "not_attempted"
        if not fixed_object_baseline
        else "inconclusive"
        if not fixed_baseline_known
        else "absent"
        if baseline_identity is None
        else "present"
    )
    attempt_diagnostics["stable_baseline"] = (
        "not_attempted"
        if announcement_baseline is None
        else "inconclusive"
        if not stable_announcement_baseline_known
        else "absent"
        if stable_announcement_baseline_identity is None
        else "present"
    )

    generation_requested_at_ms: int | None = None

    def mark_generation_dispatched() -> None:
        nonlocal generation_requested_at_ms
        if generation_requested_at_ms is None:
            generation_requested_at_ms = int(time.time() * 1000)

    generation_acknowledged = False
    try:
        (
            yield MowerAction(
                payload={"m": "a", "p": 0, "o": 10, "d": {"idx": map_index}},
                operation="start point-cloud generation",
                deadline=deadline,
                require_data=False,
                on_dispatch=mark_generation_dispatched,
            )
        )
        generation_acknowledged = True
        attempt_diagnostics["generation_result"] = "accepted"
    except DreameLawnMowerPointCloudError as err:
        attempt_diagnostics["generation_result"] = f"error:{err.code}"
        attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
        err.diagnostic_context.update(attempt_diagnostics)
        if generation_requested_at_ms is None or err.code not in {
            "point_cloud_timeout",
            "point_cloud_mower_request_failed",
            "point_cloud_mower_response_invalid",
        }:
            raise
        # The transport lost the reply after dispatch. Never resend the
        # generation mutation: the mower may already be uploading. Rejoin
        # the same announcement/object polling flow instead.
    if generation_requested_at_ms is None:
        # Preserve compatibility with alternate/mock cloud transports that
        # do not expose the dispatch hook.
        mark_generation_dispatched()
    assert generation_requested_at_ms is not None
    record_point_cloud_stage("generation_reply", attempt_diagnostics)

    def acknowledged_fixed_object(object_name: str | None) -> bool:
        """Return whether an exact-model action ack proves fixed-key refresh."""
        extension = (
            _app_object_extension(object_name) if object_name is not None else None
        )
        return (
            extension is not None
            and extension.casefold() == "bin"
            and account_type == "mova"
            and generation_acknowledged
            and model.strip().casefold()
            in _POINT_CLOUD_ACKNOWLEDGED_FIXED_OBJECT_MODELS
        )

    acknowledged_fixed_object_model = (
        account_type == "mova"
        and generation_acknowledged
        and model.strip().casefold() in _POINT_CLOUD_ACKNOWLEDGED_FIXED_OBJECT_MODELS
    )

    observed_clear = baseline_known and baseline_name is None
    saw_unusable_point_cloud = False
    saw_stale_point_cloud = False
    saw_unverified_fixed_object = False
    # Pre-generation baselines do not prove that post-dispatch reads work.
    # Track the latest result per required route: a later successful read
    # can recover a transient failure, but an old empty read cannot mask it.
    attempt_diagnostics["announcement_poll_result"] = "not_attempted"
    attempt_diagnostics["indexed_poll_result"] = "not_attempted"
    indexed_verification_required = False
    rejected_object_names: set[str] = set()
    object_download_attempts: dict[str, int] = {}
    announcement_download_attempts: dict[tuple[str, int], int] = {}
    announcement_reprobe_attempts = 0
    indexed_announcement_name = None
    stable_announcement_verification_attempts = 0
    stable_announcement_verification_after = 0.0
    while time.monotonic() < deadline:
        record_point_cloud_stage("poll_result", attempt_diagnostics)
        latest_announcement: dict[str, Any] = {}
        announced_name = None
        announced_identity = None
        announcement_polled = False
        observed_announcement_capability: bool | None = None
        if use_announcement_path:
            (
                observed_announcement_capability,
                announced_name,
                announced_identity,
            ) = yield ReadAnnouncement(
                requested_after_ms=generation_requested_at_ms,
                baseline=announcement_baseline,
                require_post_request=announcement_baseline is None,
                deadline=deadline,
                observation=latest_announcement,
            )
            announcement_polled = True
        elif announcement_probe_pending:
            # A transient cloud timeout during the preflight probe must not
            # permanently select the legacy OBJ route. Keep that fallback
            # active while re-probing the dedicated announcement property
            # after generation has started.
            remaining = max(0.0, deadline - time.monotonic())
            reprobe_budget = min(
                _POINT_CLOUD_ANNOUNCEMENT_REPROBE_TIMEOUT_SECONDS,
                remaining,
            )
            (
                announcement_capability,
                announced_name,
                announced_identity,
            ) = yield ReadAnnouncement(
                requested_after_ms=generation_requested_at_ms,
                baseline=announcement_baseline,
                require_post_request=announcement_baseline is None,
                fallback_reserve_seconds=max(
                    0.0,
                    remaining - reprobe_budget,
                ),
                deadline=deadline,
                observation=latest_announcement,
            )
            announcement_polled = True
            announcement_reprobe_attempts += 1
            if announcement_capability is True:
                use_announcement_path = True
                announcement_probe_pending = False
            elif announcement_capability is False:
                announcement_probe_pending = False
            elif (
                announcement_reprobe_attempts
                >= _POINT_CLOUD_ANNOUNCEMENT_REPROBE_ATTEMPTS
            ):
                # Do not keep constraining a valid but slower legacy OBJ
                # route when the dedicated property remains inconclusive.
                announcement_probe_pending = False
            observed_announcement_capability = announcement_capability
        if (
            announcement_polled
            and latest_announcement.get("status") != "budget_exhausted"
        ):
            # A call started just before the outer deadline can run out of
            # budget before it reaches the cloud transport. Preserve the
            # last actual property observation instead of replacing useful
            # diagnostics with that scheduling race.
            attempt_diagnostics["latest_announcement"] = latest_announcement
            attempt_diagnostics["announcement_poll_result"] = (
                "observed"
                if observed_announcement_capability is True
                and (
                    latest_announcement.get("status") in {"fresh", "stale"}
                    or latest_announcement.get("value_shape")
                    in {
                        "null",
                        "empty_string",
                    }
                )
                else "inconclusive"
            )
            if observed_announcement_capability is not None:
                attempt_diagnostics["announcement_capability"] = (
                    "available" if observed_announcement_capability else "unavailable"
                )
            attempt_diagnostics["announcement_polls"] += 1
            if announced_name is not None:
                attempt_diagnostics["announcement_fresh_observations"] += 1
            elif announced_identity is not None:
                attempt_diagnostics["announcement_stale_observations"] += 1
            elif observed_announcement_capability is False:
                attempt_diagnostics["announcement_unavailable_observations"] += 1
            elif observed_announcement_capability is None:
                attempt_diagnostics["announcement_inconclusive_observations"] += 1
            else:
                attempt_diagnostics["announcement_empty_observations"] += 1
        stable_announced_name = None
        stable_announcement_observed = (
            announced_name is None
            and announced_identity is not None
            and announcement_baseline is not None
            and announced_identity == announcement_baseline
        )
        needs_indexed_verification = stable_announcement_observed or (
            use_announcement_path
            and announced_name is None
            and acknowledged_fixed_object_model
        )
        indexed_verification_required = needs_indexed_verification
        if needs_indexed_verification and (
            indexed_announcement_name is None
            and time.monotonic() >= stable_announcement_verification_after
        ):
            # Do not add an OBJ round trip to the normal fresh-property
            # path. Query it only when the firmware actually presents an
            # unchanged announcement after accepting generation.
            verification_deadline = min(
                deadline,
                time.monotonic() + _POINT_CLOUD_LEGACY_POLL_TIMEOUT_SECONDS,
            )
            indexed_verification_error: str | None = None
            try:
                indexed_result = yield MowerAction(
                    payload={"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                    operation="verify the requested point-cloud object identity",
                    deadline=verification_deadline,
                    require_data=True,
                )
            except DreameLawnMowerPointCloudError as err:
                attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
                indexed_result = None
                indexed_verification_error = err.code
                attempt_diagnostics["indexed_verification_result"] = f"error:{err.code}"
            attempt_diagnostics["latest_indexed"] = indexed_object_observation(
                indexed_result,
                map_index,
            )
            attempt_diagnostics["indexed_poll_result"] = (
                indexed_poll_result(attempt_diagnostics["latest_indexed"])
                if indexed_verification_error is None
                else "inconclusive"
            )
            observed_indexed_name = _point_cloud_object_name(
                indexed_result,
                map_index,
            )
            observed_extension = (
                _app_object_extension(observed_indexed_name)
                if observed_indexed_name is not None
                else None
            )
            attempt_diagnostics["latest_indexed"]["object_extension"] = (
                "missing"
                if observed_extension is None
                else observed_extension.casefold()
                if observed_extension.casefold() in accepted_extensions
                else "unsupported"
            )
            stable_announcement_verification_attempts += 1
            attempt_diagnostics["indexed_verification_attempts"] = (
                stable_announcement_verification_attempts
            )
            if (
                stable_announcement_observed
                and announced_identity is not None
                and observed_indexed_name == announced_identity[0]
            ):
                indexed_announcement_name = observed_indexed_name
                attempt_diagnostics["indexed_verification_result"] = (
                    "matched_announcement"
                )
            elif acknowledged_fixed_object(observed_indexed_name):
                # VIAX can expose 99.20 without a usable pre-generation
                # identity, or use a differently scoped name than OBJ.
                # A positive o:10 reply plus an indexed fixed object is the
                # same exact-model freshness boundary as the legacy route.
                indexed_announcement_name = observed_indexed_name
                attempt_diagnostics["indexed_verification_result"] = (
                    "accepted_acknowledged_fixed_object"
                )
            else:
                if observed_indexed_name is None and indexed_verification_error is None:
                    attempt_diagnostics["indexed_verification_result"] = (
                        "object_not_observed"
                    )
                elif observed_indexed_name is not None:
                    attempt_diagnostics["indexed_verification_result"] = (
                        "object_mismatch"
                    )
                # OBJ can lag the accepted upload request or fail through
                # a transient routed-cloud timeout. Keep verification
                # retryable instead of polling only 99.20 until expiry.
                stable_announcement_verification_after = time.monotonic() + min(
                    2.0,
                    max(poll_interval, 0.25)
                    * (2 ** min(stable_announcement_verification_attempts, 3)),
                )
        if announced_name is None and indexed_announcement_name is not None:
            # Firmware 4.3.6_0625 can keep both the 99.20 object name and
            # updateDate unchanged after accepting o:10. The signer is
            # activated for the requested indexed object instead. Only
            # accept that stable key when a live post-dispatch OBJ read
            # maps it to the requested map index.
            stable_announced_name = indexed_announcement_name
        download_name = announced_name or stable_announced_name
        if download_name is not None:
            attempt_diagnostics["download_attempts"] += 1
            attempt_key = announced_identity or (download_name, 0)
            attempts = announcement_download_attempts.get(attempt_key, 0)
            announcement_download_attempts[attempt_key] = attempts + 1
            try:
                content, content_type, object_identity = yield DownloadObject(
                    object_name=download_name,
                    deadline=deadline,
                    download_timeout=download_timeout,
                    max_bytes=max_bytes,
                    observation=attempt_diagnostics,
                )
                if stable_announced_name is not None:
                    stable_refresh_proven = acknowledged_fixed_object(
                        stable_announced_name
                    ) or (
                        stable_announcement_baseline_known
                        and (
                            stable_announcement_baseline_identity is None
                            or object_identity.differs_from(
                                stable_announcement_baseline_identity
                            )
                        )
                    )
                    if not stable_refresh_proven:
                        if not stable_announcement_baseline_known:
                            raise DreameLawnMowerPointCloudError(
                                "The stable point-cloud baseline could not "
                                "be read before generation.",
                                code="point_cloud_download_invalid",
                            )
                        saw_stale_point_cloud = True
                        raise DreameLawnMowerPointCloudError(
                            "The stable point-cloud object has not changed "
                            "since the generation request.",
                            code="point_cloud_not_published",
                        )
                attempt_diagnostics["last_download_step"] = "validation"
                metadata = yield ParseMetadata(
                    content=content, max_bytes=max_bytes, deadline=deadline
                )
            except (DeviceException, DreameLawnMowerPointCloudError) as err:
                if isinstance(err, DreameLawnMowerPointCloudError):
                    attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
                attempt_diagnostics["last_download_result"] = (
                    f"error:{err.code}"
                    if isinstance(err, DreameLawnMowerPointCloudError)
                    else "error:device"
                )
                # The mower announces the object before upload progress
                # reaches 100%, so the signer can briefly return no URL.
                if (
                    isinstance(err, DreameLawnMowerPointCloudError)
                    and err.code == "point_cloud_not_published"
                ):
                    saw_stale_point_cloud = True
                else:
                    saw_unusable_point_cloud = True
                retry_delay = min(
                    _POINT_CLOUD_ANNOUNCEMENT_RETRY_MAX_SECONDS,
                    max(poll_interval, 0.5) * (2 ** min(attempts, 4)),
                )
            else:
                attempt_diagnostics["last_download_result"] = "validated"
                return DreameLawnMowerPointCloudDownload(
                    map_index=map_index,
                    content=content,
                    metadata=metadata,
                    content_type=content_type,
                )

            remaining = deadline - time.monotonic()
            if remaining > 0:
                (yield WaitForObject(seconds=min(retry_delay, remaining)))
            continue

        remaining = deadline - time.monotonic()
        if use_announcement_path:
            # A2-class firmware can take a few seconds to upload the LiDAR
            # object after accepting o:10. Avoid slow routed OBJ reads while
            # its dedicated announcement channel is active. Do not accept
            # an unverified legacy OBJ as fresh when no OBJ baseline exists.
            if remaining > 0:
                (yield WaitForObject(seconds=min(poll_interval, remaining)))
            continue

        object_deadline = deadline
        if announcement_probe_pending:
            object_deadline = min(
                deadline,
                time.monotonic() + _POINT_CLOUD_LEGACY_POLL_TIMEOUT_SECONDS,
            )
            if object_deadline <= time.monotonic():
                continue
        try:
            object_result = yield MowerAction(
                payload={"m": "g", "t": "OBJ", "d": {"type": "3dmap"}},
                operation="read the generated point-cloud object state",
                deadline=object_deadline,
                require_data=True,
            )
        except DreameLawnMowerPointCloudError as err:
            attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
            err.diagnostic_context.update(attempt_diagnostics)
            attempt_diagnostics["indexed_poll_result"] = "inconclusive"
            if err.code in {
                "point_cloud_timeout",
                "point_cloud_mower_request_failed",
            }:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    (yield WaitForObject(seconds=min(poll_interval, remaining)))
                continue
            raise
        object_name = _point_cloud_object_name(
            object_result,
            map_index,
        )
        attempt_diagnostics["latest_indexed"] = indexed_object_observation(
            object_result,
            map_index,
        )
        attempt_diagnostics["indexed_poll_result"] = indexed_poll_result(
            attempt_diagnostics["latest_indexed"]
        )
        if attempt_diagnostics["indexed_poll_result"] != "observed":
            # An unfamiliar slot is not a clear or a usable baseline.
            # Preserve freshness state until the mower supplies one.
            remaining = deadline - time.monotonic()
            if remaining > 0:
                (yield WaitForObject(seconds=min(poll_interval, remaining)))
            continue
        if not baseline_known:
            # A bounded preflight timeout is not proof that the prior OBJ
            # state was empty. Establish the missing baseline from the
            # first successful legacy response before accepting a change.
            baseline_name = object_name
            baseline_known = True
            observed_clear = object_name is None
            remaining = deadline - time.monotonic()
            if remaining > 0:
                (yield WaitForObject(seconds=min(poll_interval, remaining)))
            continue
        if object_name is None:
            observed_clear = True
        object_extension = (
            _app_object_extension(object_name) if object_name is not None else None
        )
        attempt_diagnostics["latest_indexed"]["object_extension"] = (
            "missing"
            if object_extension is None
            else object_extension.casefold()
            if object_extension.casefold() in accepted_extensions
            else "unsupported"
        )
        fixed_object = (
            object_name is not None
            and object_name == baseline_name
            and not observed_clear
            and object_extension is not None
            and object_extension.casefold() == "bin"
        )
        object_ready = object_name != baseline_name or observed_clear or fixed_object
        attempt_allowed = object_name is not None and (
            fixed_object
            or (
                object_name not in rejected_object_names
                and object_download_attempts.get(object_name, 0) < 2
            )
        )
        if (
            object_name is not None
            and object_extension is not None
            and object_extension.casefold() in accepted_extensions
            and object_ready
            and attempt_allowed
        ):
            attempt_diagnostics["download_attempts"] += 1
            if not fixed_object:
                object_download_attempts[object_name] = (
                    object_download_attempts.get(object_name, 0) + 1
                )
            try:
                content, content_type, object_identity = yield DownloadObject(
                    object_name=object_name,
                    deadline=deadline,
                    download_timeout=download_timeout,
                    max_bytes=max_bytes,
                    observation=attempt_diagnostics,
                )
            except (DeviceException, DreameLawnMowerPointCloudError) as err:
                if isinstance(err, DreameLawnMowerPointCloudError):
                    attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
                attempt_diagnostics["last_download_result"] = (
                    f"error:{err.code}"
                    if isinstance(err, DreameLawnMowerPointCloudError)
                    else "error:device"
                )
                saw_unusable_point_cloud = True
            else:
                # MOVA can keep a fixed object byte-for-byte deterministic
                # after o:10. A successful action reply is the only safe
                # substitute for an observable object-identity change.
                acknowledged_mova_fixed_object = (
                    fixed_object and acknowledged_fixed_object(object_name)
                )
                if fixed_object and not fixed_baseline_known:
                    attempt_diagnostics["last_download_result"] = (
                        "rejected:fixed_baseline_inconclusive"
                    )
                    saw_unverified_fixed_object = True
                elif (
                    fixed_object
                    and baseline_identity is not None
                    and not object_identity.differs_from(baseline_identity)
                    and not acknowledged_mova_fixed_object
                ):
                    attempt_diagnostics["last_download_result"] = (
                        "rejected:stale_object"
                    )
                    saw_stale_point_cloud = True
                else:
                    try:
                        attempt_diagnostics["last_download_step"] = "validation"
                        metadata = yield ParseMetadata(
                            content=content, max_bytes=max_bytes, deadline=deadline
                        )
                    except DreameLawnMowerPointCloudError as err:
                        attempt_diagnostics.update(err.safe_diagnostics()["attempt"])
                        attempt_diagnostics["last_download_result"] = (
                            f"error:{err.code}"
                        )
                        saw_unusable_point_cloud = True
                        if not fixed_object:
                            rejected_object_names.add(object_name)
                    else:
                        attempt_diagnostics["last_download_result"] = "validated"
                        return DreameLawnMowerPointCloudDownload(
                            map_index=map_index,
                            content=content,
                            metadata=metadata,
                            content_type=content_type,
                        )

        remaining = deadline - time.monotonic()
        if remaining > 0:
            (yield WaitForObject(seconds=min(poll_interval, remaining)))

    record_point_cloud_stage("poll_result", attempt_diagnostics)
    if saw_unverified_fixed_object:
        raise DreameLawnMowerPointCloudError(
            "The fixed point-cloud object could not be compared with "
            "its pre-generation version before the timeout.",
            code="point_cloud_download_invalid",
            stage="download_validation",
            public_message=(
                "Home Assistant could not verify that the mower refreshed "
                f"its 3D map within {timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
            diagnostic_reason="fixed_baseline_inconclusive",
            discovery_route="legacy_obj",
            generation_acknowledged=generation_acknowledged,
            diagnostic_context=attempt_diagnostics,
        )

    if saw_unusable_point_cloud:
        raise DreameLawnMowerPointCloudError(
            "The mower published a point cloud, but it could not be downloaded "
            "and validated before the timeout.",
            code="point_cloud_download_invalid",
            stage="download_validation",
            public_message=(
                "The mower published a 3D map, but Home Assistant could not "
                f"download and validate it within {timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
            diagnostic_reason="published_object_invalid",
            discovery_route=(
                "announcement_property" if use_announcement_path else "legacy_obj"
            ),
            generation_acknowledged=generation_acknowledged,
            diagnostic_context=attempt_diagnostics,
        )

    if (
        use_announcement_path
        and attempt_diagnostics["announcement_poll_result"] != "observed"
        or (not use_announcement_path or indexed_verification_required)
        and attempt_diagnostics["indexed_poll_result"] != "observed"
    ):
        raise DreameLawnMowerPointCloudError(
            "Point-cloud discovery ended without a conclusive final read.",
            code="point_cloud_timeout",
            stage="generation",
            public_message=(
                "Home Assistant could not confirm whether the mower published "
                f"a fresh 3D map within {timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
            diagnostic_reason="polling_inconclusive",
            discovery_route=(
                "announcement_property" if use_announcement_path else "legacy_obj"
            ),
            generation_acknowledged=generation_acknowledged,
            diagnostic_context=attempt_diagnostics,
        )

    if saw_stale_point_cloud:
        raise DreameLawnMowerPointCloudError(
            "The mower's fixed point-cloud object did not refresh before the timeout.",
            code="point_cloud_not_published",
            stage="generation",
            public_message=(
                f"The mower did not publish a fresh 3D map within {timeout:g} seconds."
            ),
            timeout_seconds=timeout,
            retry_after_seconds=10,
            diagnostic_reason="unchanged_object",
            retryable=False,
            discovery_route=(
                "announcement_property" if use_announcement_path else "legacy_obj"
            ),
            generation_acknowledged=generation_acknowledged,
            diagnostic_context=attempt_diagnostics,
        )

    raise DreameLawnMowerPointCloudError(
        "The mower did not publish or refresh a point cloud before the timeout.",
        code="point_cloud_not_published",
        stage="generation",
        public_message=(
            f"The mower did not publish a fresh 3D map within {timeout:g} seconds."
        ),
        timeout_seconds=timeout,
        retry_after_seconds=10,
        diagnostic_reason="object_not_observed",
        retryable=False,
        discovery_route=(
            "announcement_property" if use_announcement_path else "legacy_obj"
        ),
        generation_acknowledged=generation_acknowledged,
        diagnostic_context=attempt_diagnostics,
    )
