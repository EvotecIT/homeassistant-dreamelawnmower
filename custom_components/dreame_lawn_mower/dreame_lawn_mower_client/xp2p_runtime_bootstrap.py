"""Install the pinned Linux XP2P host runtime from verified upstream files."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
import platform
import shutil
import struct
import tarfile
import tempfile
import threading
import zipfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Protocol

import requests

from .video_runtime import DreameLawnMowerVideoRuntimeError
from .xp2p_host_probe import probe_xp2p_host_worker
from .xp2p_host_runtime import DreameLawnMowerXp2pHostAssets
from .xp2p_host_worker_blob import (
    WORKER_GZIP_BASE64,
    WORKER_GZIP_SHA256,
    WORKER_SHA256,
)

RUNTIME_LAYOUT_VERSION = "1"
LARGE_PAGE_RUNTIME_LAYOUT_VERSION = "2"
TENCENT_XP2P_VERSION = "2.4.50"
TENCENT_XP2P_AAR_URL = (
    "https://repo.maven.apache.org/maven2/com/tencent/iot/thirdparty/android/"
    "xp2p-sdk/2.4.50/xp2p-sdk-2.4.50.aar"
)
TENCENT_XP2P_AAR_SHA256 = (
    "7cbbacdb08861a16d5d9f1db71f2b04d7b9d01e8c71b20e51612100c2098dc37"
)
TENCENT_XP2P_LIBRARY_SHA256 = (
    "30fafc9f7b15a29a64622200817248c636133e06affc40479084ca3d8d188649"
)

AOSP_RUNTIME_COMMIT = "070571b455076f77a01c7b07154a15e545d2b428"
AOSP_RUNTIME_APEX_URL = (
    "https://android.googlesource.com/platform/prebuilts/runtime/+/"
    f"{AOSP_RUNTIME_COMMIT}/mainline/runtime/apex/"
    "com.android.runtime-arm64.apex?format=TEXT"
)
AOSP_RUNTIME_APEX_SHA256 = (
    "83bf0dce249728dae48149b80d28b48115c54adad95a352120d58a6ac669d1fc"
)
AOSP_RUNTIME_FILES = {
    "bin/linker64": (
        "bin/linker64",
        "dc36b5feed26752e8f0758cff8d6dcdd63b1434c03d6692b58ba5c09399b1d16",
        0o755,
    ),
    "lib64/bionic/libc.so": (
        "lib/bionic/libc.so",
        "9f1f93bbd186af5c40a93f6ca4425bfdce82b7f3b9c730e59bb5c7fa598593cf",
        0o644,
    ),
    "lib64/bionic/libm.so": (
        "lib/bionic/libm.so",
        "74ec94e6aecf17ab8d56d2da151dd0756e21e8f53e567815cbf43c5266224c5b",
        0o644,
    ),
    "lib64/bionic/libdl.so": (
        "lib/bionic/libdl.so",
        "7e215e0e68372d779492438d1a8e0e53e63154812d506af830e6c3dbcaeb0607",
        0o644,
    ),
    "lib64/ld-android.so": (
        "lib/ld-android.so",
        "00dc38b8ce57d92b74c404fb40c37c50527fc558dd497bb484a950e926d63bff",
        0o644,
    ),
    "lib64/libc++.so": (
        "lib/libstdc++.so",
        "ae87fa079200affdb3c534c794bcb213397e75120c5e126202b4644acad09629",
        0o644,
    ),
}

AOSP_VNDK_COMMIT = "1a059a5a203352d3e0c2fd3ccff5719cc37fc340"
_AOSP_VNDK_BASE = (
    "https://android.googlesource.com/platform/prebuilts/vndk/v31/+/"
    f"{AOSP_VNDK_COMMIT}/arm64/arch-arm64-armv8-a/shared"
)
AOSP_VNDK_FILES = {
    "lib/liblog.so": (
        f"{_AOSP_VNDK_BASE}/llndk-stub/liblog.so?format=TEXT",
        "e0099f476029f7443ba1829daa7100becd03ae08a3e3cd957034add9003c6eae",
    ),
    "lib/libz.so": (
        f"{_AOSP_VNDK_BASE}/vndk-sp/libz.so?format=TEXT",
        "68056ee2e644754f7693eb903b6fa51819b6655ff8a82a0c58e56aff3ff48e31",
    ),
}

QEMU_VERSION = "7.2.0-1"
QEMU_X86_64_URL = (
    "https://github.com/multiarch/qemu-user-static/releases/download/"
    "v7.2.0-1/x86_64_qemu-aarch64-static.tar.gz"
)
QEMU_X86_64_ARCHIVE_SHA256 = (
    "b5dd968d522fd5d615d27c77fb1ca0847455d0b8badd7492647e7ab674339247"
)
QEMU_X86_64_BINARY_SHA256 = (
    "dce64b2dc6b005485c7aa735a7ea39cb0006bf7e5badc28b324b2cd0c73d883f"
)
QEMU_AARCH64_VERSION = "10.0.0-r1"
QEMU_AARCH64_APK_URL = (
    "https://dl-cdn.alpinelinux.org/alpine/v3.22/community/aarch64/"
    "qemu-aarch64-10.0.0-r1.apk"
)
QEMU_AARCH64_APK_SHA256 = (
    "59cdf1518f501c87f0397324bf97c807adac45fe70a50f5468fcb55280573758"
)
QEMU_AARCH64_BINARY_SHA256 = (
    "4d1b11f85b7617ee9209d4e9224378ad8d96f757dc30ee7939f8097c1b65b13a"
)

_TENCENT_AAR_LIBRARY = "jni/arm64-v8a/libiot_video_demo.so"
_MANIFEST_NAME = "runtime-manifest.json"
_INSTALL_LOCK = threading.Lock()
_EXT4_EXTENTS_FLAG = 0x00080000
_EXTENT_MAGIC = 0xF30A


class _HttpResponse(Protocol):
    status_code: int
    content: bytes


class _HttpClient(Protocol):
    def get(self, url: str, *, timeout: float) -> _HttpResponse: ...


def ensure_xp2p_host_runtime(
    root: str | Path,
    *,
    machine: str | None = None,
    page_size: int | None = None,
    http_client: _HttpClient | None = None,
    timeout: float = 60.0,
) -> DreameLawnMowerXp2pHostAssets:
    """Return a complete, verified XP2P runtime, installing it when absent."""
    architecture = _normalize_machine(machine or platform.machine())
    if architecture not in {"aarch64", "x86_64"}:
        raise DreameLawnMowerVideoRuntimeError(
            "Managed XP2P video supports Linux aarch64 and x86_64 hosts; "
            f"this host reports {machine or platform.machine()!r}."
        )
    host_page_size = _host_page_size() if page_size is None else page_size
    use_aarch64_self_qemu = (
        architecture == "aarch64" and host_page_size > 4096
    )
    layout_version = (
        LARGE_PAGE_RUNTIME_LAYOUT_VERSION
        if use_aarch64_self_qemu
        else RUNTIME_LAYOUT_VERSION
    )
    root_path = Path(root)
    runtime_path = root_path / f"runtime-v{layout_version}-{architecture}"
    with _INSTALL_LOCK:
        assets = _validated_assets(
            runtime_path,
            architecture,
            layout_version=layout_version,
            include_aarch64_qemu=use_aarch64_self_qemu,
        )
        if assets is None:
            root_path.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{runtime_path.name}-",
                    dir=root_path,
                )
            )
            try:
                _install_runtime(
                    staging,
                    architecture,
                    layout_version=layout_version,
                    include_aarch64_qemu=use_aarch64_self_qemu,
                    http_client=http_client or requests,
                    timeout=timeout,
                )
                installed = _validated_assets(
                    staging,
                    architecture,
                    layout_version=layout_version,
                    include_aarch64_qemu=use_aarch64_self_qemu,
                )
                if installed is None:
                    raise DreameLawnMowerVideoRuntimeError(
                        "Installed XP2P host runtime failed integrity validation."
                    )
                if runtime_path.exists():
                    _remove_runtime_path(runtime_path, root_path)
                staging.replace(runtime_path)
            except Exception:
                _remove_runtime_path(staging, root_path)
                raise
            assets = _validated_assets(
                runtime_path,
                architecture,
                layout_version=layout_version,
                include_aarch64_qemu=use_aarch64_self_qemu,
            )
            if assets is None:
                raise DreameLawnMowerVideoRuntimeError(
                    "XP2P host runtime disappeared after installation."
                )
    return _with_startup_probe(assets)


def _host_page_size() -> int:
    """Return the Linux host page size, or zero when it cannot be read."""
    try:
        return int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        return 0


def _with_startup_probe(
    assets: DreameLawnMowerXp2pHostAssets,
) -> DreameLawnMowerXp2pHostAssets:
    """Attach one safe worker-launch result to the prepared runtime."""
    return replace(
        assets,
        startup_probe=probe_xp2p_host_worker(
            assets.command(),
            assets.environment(),
        ),
    )


def _install_runtime(
    target: Path,
    architecture: str,
    *,
    layout_version: str = RUNTIME_LAYOUT_VERSION,
    include_aarch64_qemu: bool = False,
    http_client: _HttpClient,
    timeout: float,
) -> None:
    worker_gzip = base64.b64decode(WORKER_GZIP_BASE64, validate=True)
    _require_hash(worker_gzip, WORKER_GZIP_SHA256, "embedded XP2P worker")
    worker = gzip.decompress(worker_gzip)
    _write_verified(
        target / "bin/dreame-xp2p-host-runner",
        worker,
        WORKER_SHA256,
        mode=0o755,
    )

    aar = _download_verified(
        http_client,
        TENCENT_XP2P_AAR_URL,
        TENCENT_XP2P_AAR_SHA256,
        timeout=timeout,
        label="Tencent XP2P SDK",
    )
    try:
        with zipfile.ZipFile(io.BytesIO(aar)) as archive:
            xp2p_library = archive.read(_TENCENT_AAR_LIBRARY)
    except (KeyError, zipfile.BadZipFile) as err:
        raise DreameLawnMowerVideoRuntimeError(
            "Tencent XP2P SDK archive did not contain its aarch64 library."
        ) from err
    _write_verified(
        target / "lib/libiot_video_demo.so",
        xp2p_library,
        TENCENT_XP2P_LIBRARY_SHA256,
    )

    apex_encoded = _download_verified(
        http_client,
        AOSP_RUNTIME_APEX_URL,
        None,
        timeout=timeout,
        label="AOSP Bionic runtime",
    )
    try:
        apex = _decode_gitiles(apex_encoded)
    except ValueError as err:
        raise DreameLawnMowerVideoRuntimeError(
            "AOSP Bionic runtime download was not valid Gitiles base64."
        ) from err
    _require_hash(apex, AOSP_RUNTIME_APEX_SHA256, "AOSP Bionic runtime")
    try:
        with zipfile.ZipFile(io.BytesIO(apex)) as archive:
            filesystem_image = archive.read("apex_payload.img")
    except (KeyError, zipfile.BadZipFile) as err:
        raise DreameLawnMowerVideoRuntimeError(
            "AOSP Bionic APEX did not contain apex_payload.img."
        ) from err
    filesystem = _ExtFilesystem(filesystem_image)
    for source, (relative_target, expected_hash, mode) in AOSP_RUNTIME_FILES.items():
        _write_verified(
            target / relative_target,
            filesystem.read_file(source),
            expected_hash,
            mode=mode,
        )

    for relative_target, (url, expected_hash) in AOSP_VNDK_FILES.items():
        encoded = _download_verified(
            http_client,
            url,
            None,
            timeout=timeout,
            label=Path(relative_target).name,
        )
        try:
            content = _decode_gitiles(encoded)
        except ValueError as err:
            raise DreameLawnMowerVideoRuntimeError(
                f"AOSP {Path(relative_target).name} was not valid Gitiles base64."
            ) from err
        _write_verified(target / relative_target, content, expected_hash)

    if architecture == "x86_64":
        qemu_archive = _download_verified(
            http_client,
            QEMU_X86_64_URL,
            QEMU_X86_64_ARCHIVE_SHA256,
            timeout=timeout,
            label="qemu-aarch64",
        )
        try:
            with tarfile.open(fileobj=io.BytesIO(qemu_archive), mode="r:gz") as archive:
                member = archive.getmember("qemu-aarch64-static")
                stream = archive.extractfile(member)
                qemu = stream.read() if stream is not None else b""
        except (KeyError, tarfile.TarError) as err:
            raise DreameLawnMowerVideoRuntimeError(
                "qemu-aarch64 archive was malformed."
            ) from err
        _write_verified(
            target / "bin/qemu-aarch64-static",
            qemu,
            QEMU_X86_64_BINARY_SHA256,
            mode=0o755,
        )
    elif include_aarch64_qemu:
        qemu_apk = _download_verified(
            http_client,
            QEMU_AARCH64_APK_URL,
            QEMU_AARCH64_APK_SHA256,
            timeout=timeout,
            label="Alpine qemu-aarch64",
        )
        try:
            with tarfile.open(
                fileobj=io.BytesIO(qemu_apk),
                mode="r:gz",
            ) as archive:
                stream = archive.extractfile("usr/bin/qemu-aarch64")
                qemu = stream.read() if stream is not None else b""
        except (KeyError, tarfile.TarError) as err:
            raise DreameLawnMowerVideoRuntimeError(
                "Alpine qemu-aarch64 package was malformed."
            ) from err
        _write_verified(
            target / "bin/qemu-aarch64-static",
            qemu,
            QEMU_AARCH64_BINARY_SHA256,
            mode=0o755,
        )

    manifest = {
        "layout_version": layout_version,
        "architecture": architecture,
        "tencent_xp2p_version": TENCENT_XP2P_VERSION,
        "aosp_runtime_commit": AOSP_RUNTIME_COMMIT,
        "aosp_vndk_commit": AOSP_VNDK_COMMIT,
        "qemu_version": (
            QEMU_VERSION
            if architecture == "x86_64"
            else QEMU_AARCH64_VERSION
            if include_aarch64_qemu
            else None
        ),
        "files": _expected_installed_hashes(
            architecture,
            include_aarch64_qemu=include_aarch64_qemu,
        ),
    }
    manifest_path = target / _MANIFEST_NAME
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _validated_assets(
    runtime_path: Path,
    architecture: str,
    *,
    layout_version: str = RUNTIME_LAYOUT_VERSION,
    include_aarch64_qemu: bool = False,
) -> DreameLawnMowerXp2pHostAssets | None:
    manifest_path = runtime_path / _MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(manifest, Mapping):
        return None
    if manifest.get("layout_version") != layout_version:
        return None
    if manifest.get("architecture") != architecture:
        return None
    expected = _expected_installed_hashes(
        architecture,
        include_aarch64_qemu=include_aarch64_qemu,
    )
    if manifest.get("files") != expected:
        return None
    for relative, expected_hash in expected.items():
        path = runtime_path / relative
        try:
            if _file_hash(path) != expected_hash:
                return None
        except OSError:
            return None
    binary = runtime_path / "bin"
    library = runtime_path / "lib"
    assets = DreameLawnMowerXp2pHostAssets(
        worker_path=binary / "dreame-xp2p-host-runner",
        linker_path=binary / "linker64",
        library_path=library / "libiot_video_demo.so",
        library_search_paths=(library, library / "bionic"),
        qemu_path=(
            binary / "qemu-aarch64-static"
            if architecture == "x86_64" or include_aarch64_qemu
            else None
        ),
    )
    try:
        assets.validate()
    except DreameLawnMowerVideoRuntimeError:
        return None
    return assets


def _expected_installed_hashes(
    architecture: str,
    *,
    include_aarch64_qemu: bool = False,
) -> dict[str, str]:
    files = {
        "bin/dreame-xp2p-host-runner": WORKER_SHA256,
        "lib/libiot_video_demo.so": TENCENT_XP2P_LIBRARY_SHA256,
        **{
            relative_target: expected_hash
            for relative_target, expected_hash, _mode in AOSP_RUNTIME_FILES.values()
        },
        **{
            relative_target: expected_hash
            for relative_target, (_url, expected_hash) in AOSP_VNDK_FILES.items()
        },
    }
    if architecture == "x86_64":
        files["bin/qemu-aarch64-static"] = QEMU_X86_64_BINARY_SHA256
    elif include_aarch64_qemu:
        files["bin/qemu-aarch64-static"] = QEMU_AARCH64_BINARY_SHA256
    return dict(sorted(files.items()))


def _download_verified(
    client: _HttpClient,
    url: str,
    expected_hash: str | None,
    *,
    timeout: float,
    label: str,
) -> bytes:
    try:
        response = client.get(url, timeout=timeout)
    except requests.RequestException as err:
        raise DreameLawnMowerVideoRuntimeError(f"Could not download {label}.") from err
    if response.status_code != 200:
        raise DreameLawnMowerVideoRuntimeError(
            f"Could not download {label}; HTTP {response.status_code}."
        )
    content = bytes(response.content)
    if expected_hash is not None:
        _require_hash(content, expected_hash, label)
    return content


def _write_verified(
    path: Path,
    content: bytes,
    expected_hash: str,
    *,
    mode: int = 0o644,
) -> None:
    _require_hash(content, expected_hash, path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(mode)


def _require_hash(content: bytes, expected_hash: str, label: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual.casefold() != expected_hash.casefold():
        raise DreameLawnMowerVideoRuntimeError(f"{label} failed SHA-256 verification.")


def _decode_gitiles(content: bytes) -> bytes:
    return base64.b64decode(b"".join(content.split()), validate=True)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_machine(value: str) -> str:
    machine = value.strip().casefold()
    if machine in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    if machine in {"aarch64", "arm64"}:
        return "aarch64"
    return machine


def _remove_runtime_path(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved_parent = path.parent.resolve()
    if (
        resolved_parent != resolved_root
        and resolved_root not in resolved_parent.parents
    ):
        raise DreameLawnMowerVideoRuntimeError(
            "Refusing to remove XP2P runtime outside its configured root."
        )
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
        return
    shutil.rmtree(path, ignore_errors=True)


class _ExtFilesystem:
    """Small read-only ext2/4 extent reader for the pinned AOSP APEX."""

    def __init__(self, image: bytes) -> None:
        self.image = image
        if len(image) < 2048:
            raise DreameLawnMowerVideoRuntimeError(
                "AOSP Bionic filesystem image was truncated."
            )
        superblock = memoryview(image)[1024:2048]
        if struct.unpack_from("<H", superblock, 56)[0] != 0xEF53:
            raise DreameLawnMowerVideoRuntimeError(
                "AOSP Bionic payload was not an ext filesystem."
            )
        self.block_size = 1024 << struct.unpack_from("<I", superblock, 24)[0]
        self.blocks_per_group = struct.unpack_from("<I", superblock, 32)[0]
        self.inodes_per_group = struct.unpack_from("<I", superblock, 40)[0]
        self.first_data_block = struct.unpack_from("<I", superblock, 20)[0]
        self.inode_size = struct.unpack_from("<H", superblock, 88)[0] or 128
        descriptor_size = struct.unpack_from("<H", superblock, 254)[0]
        self.descriptor_size = max(descriptor_size, 32)
        self.descriptor_table = (self.first_data_block + 1) * self.block_size

    def read_file(self, path: str | PurePosixPath) -> bytes:
        """Read one absolute or root-relative regular file."""
        inode_number = 2
        parts = PurePosixPath(path).parts
        for part in parts:
            if part in {"/", "", "."}:
                continue
            entries = self._directory_entries(inode_number)
            try:
                inode_number = entries[part]
            except KeyError as err:
                raise DreameLawnMowerVideoRuntimeError(
                    f"AOSP Bionic payload omitted {path}."
                ) from err
        inode = self._inode(inode_number)
        mode = struct.unpack_from("<H", inode, 0)[0] & 0xF000
        if mode != 0x8000:
            raise DreameLawnMowerVideoRuntimeError(
                f"AOSP Bionic payload path was not a regular file: {path}."
            )
        return self._inode_data(inode)

    def _directory_entries(self, inode_number: int) -> dict[str, int]:
        inode = self._inode(inode_number)
        data = self._inode_data(inode)
        entries: dict[str, int] = {}
        offset = 0
        while offset + 8 <= len(data):
            child, record_length = struct.unpack_from("<IH", data, offset)
            name_length = data[offset + 6]
            if record_length < 8 or offset + record_length > len(data):
                break
            if child and name_length <= record_length - 8:
                name = data[offset + 8 : offset + 8 + name_length].decode(
                    "utf-8", "surrogateescape"
                )
                entries[name] = child
            offset += record_length
        return entries

    def _inode(self, inode_number: int) -> memoryview:
        if inode_number < 1:
            raise DreameLawnMowerVideoRuntimeError("Invalid ext inode number.")
        index = inode_number - 1
        group = index // self.inodes_per_group
        group_index = index % self.inodes_per_group
        descriptor_offset = self.descriptor_table + group * self.descriptor_size
        descriptor = memoryview(self.image)[
            descriptor_offset : descriptor_offset + self.descriptor_size
        ]
        table_low = struct.unpack_from("<I", descriptor, 8)[0]
        table_high = (
            struct.unpack_from("<I", descriptor, 40)[0]
            if self.descriptor_size >= 64
            else 0
        )
        table_block = table_low | (table_high << 32)
        inode_offset = table_block * self.block_size + group_index * self.inode_size
        inode = memoryview(self.image)[inode_offset : inode_offset + self.inode_size]
        if len(inode) != self.inode_size:
            raise DreameLawnMowerVideoRuntimeError("Truncated ext inode table.")
        return inode

    def _inode_data(self, inode: memoryview) -> bytes:
        size = struct.unpack_from("<I", inode, 4)[0]
        if len(inode) >= 112:
            size |= struct.unpack_from("<I", inode, 108)[0] << 32
        flags = struct.unpack_from("<I", inode, 32)[0]
        if not flags & _EXT4_EXTENTS_FLAG:
            raise DreameLawnMowerVideoRuntimeError(
                "AOSP Bionic payload used unsupported non-extent storage."
            )
        result = bytearray(size)
        for logical, physical, block_count in self._extents(inode[40:100]):
            source = physical * self.block_size
            destination = logical * self.block_size
            length = min(block_count * self.block_size, max(size - destination, 0))
            if length <= 0 or source + length > len(self.image):
                continue
            result[destination : destination + length] = self.image[
                source : source + length
            ]
        return bytes(result)

    def _extents(self, node: memoryview) -> list[tuple[int, int, int]]:
        magic, entries, maximum, depth = struct.unpack_from("<HHHH", node, 0)
        if magic != _EXTENT_MAGIC or entries > maximum:
            raise DreameLawnMowerVideoRuntimeError("Invalid ext extent tree.")
        result: list[tuple[int, int, int]] = []
        if depth == 0:
            for index in range(entries):
                offset = 12 + index * 12
                logical, length, start_high, start_low = struct.unpack_from(
                    "<IHHI", node, offset
                )
                block_count = length & 0x7FFF
                physical = start_low | (start_high << 32)
                result.append((logical, physical, block_count))
            return result
        for index in range(entries):
            offset = 12 + index * 12
            _logical, leaf_low, leaf_high, _unused = struct.unpack_from(
                "<IIHH", node, offset
            )
            leaf = leaf_low | (leaf_high << 32)
            start = leaf * self.block_size
            child = memoryview(self.image)[start : start + self.block_size]
            result.extend(self._extents(child))
        return result
