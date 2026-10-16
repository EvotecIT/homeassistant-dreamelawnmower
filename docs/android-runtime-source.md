# Managed Android runtime

The managed XP2P video runtime uses native ARM64 Android libraries on Linux. Hosts
with 16 KB memory pages use the Android 16 runtime from Google's public SDK
package `system-images;android-36;default;arm64-v8a`, revision 2. The installer
downloads a 2.54 MB subset containing the linker and seven libraries. It verifies
the bundle and every installed file with SHA-256 before accepting the runtime.

The bundle is an additional asset on the existing
[v0.2.153 release](https://github.com/EvotecIT/homeassistant-dreamelawnmower/releases/tag/v0.2.153).
Its presence there does not change that release's integration source. The runtime
source change is delivered through the normal integration release process.

The archive includes `NOTICE.txt` from the SDK image and `provenance.json`, which
records the original source, image hashes, extracted paths and file hashes. The
runtime subset does not contain the Tencent SDK or the worker executable. Those
retain their separate pinned sources and validation.

| Input | Identity |
| --- | --- |
| Google source | [arm64-v8a-36_r02.zip](https://dl.google.com/android/repository/sys-img/android/arm64-v8a-36_r02.zip) |
| SDK catalog | [Google Android system-image catalog](https://dl.google.com/android/repository/sys-img/android/sys-img2-4.xml) |
| Image size | 810,736,863 bytes |
| Vendor SHA-1 | `62ad6714df790f89c8a8ad32552ffe20bb16fe87` |
| Image SHA-256 | `c6194eb8ace128b174ea86b5fbc0c78204147123b3b5ce6d0e3d615a06ad31d1` |
| Runtime bundle SHA-256 | `4a1bc1aa00ffe8d67564e44d2d448634d3b008000e021648131f9eb7ab11d2a8` |

For reproduction, the image's `arm64-v8a/system.img` contains a GPT disk and an
Android logical-partition super image. The `system` partition is an ext4 extent
starting at byte 3,145,728, with length 940,404,736. Extract
`system/apex/com.android.runtime.apex` and `system/lib64/{ld-android.so,libc++.so,liblog.so,libz.so}`.
From the APEX's `apex_payload.img`, extract `bin/linker64` and
`lib64/bionic/{libc.so,libm.so,libdl.so}`. These eight files have 16 KB ELF load
alignment. Preserve the SDK notices when repackaging them.

The 4 KB ARM64 and x86-64 runtime paths keep their existing source pins. A new
large-page cache layout prevents an older runtime from being reused as this
candidate. Native ARM64 CI exercises the actual worker and library-loading probe;
synthetic loading checks do not establish physical mower or video-stream success.
