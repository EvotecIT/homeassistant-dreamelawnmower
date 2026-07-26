# Dreame XP2P host worker

This small AArch64/Bionic process exposes the Tencent XP2P C ABI to the Python
client without an Android device, emulator, framework, or JNI. Python sends the
temporary mower credentials through stdin, receives a local HTTP-FLV URL, and
keeps the process alive for the stream lifetime.

The checked-in source is MIT licensed with this repository. The compressed
worker embedded in `xp2p_host_worker_blob.py` is produced by `build.sh`; its
decoded SHA-256 is recorded beside the blob. The script requires an AArch64
cross compiler plus the pinned AOSP runtime files used by the Python bootstrap.
Run `embed_worker.py` with the built worker, its deterministic gzip archive,
and the target Python module to refresh the checked-in blob and both hashes.

The Python bootstrap downloads third-party binaries from their original
publishers and verifies fixed SHA-256 hashes:

- Tencent XP2P SDK 2.4.50, plus 2.4.71 on 16 KB ARM64 hosts, from Maven
  Central (Tencent binary license)
- AOSP Bionic runtime and VNDK libraries (Apache 2.0)
- qemu-user-static 7.2.0-1 on x86_64 hosts (GPL 2.0 or later)

Those third-party binaries are not committed to this repository.

The linker flags intentionally give the worker 64 KB load alignment so the
same embedded executable is valid on Linux hosts using 4 KB, 16 KB, or 64 KB
pages.
