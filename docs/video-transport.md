# Video Transport and Same-LAN Research

This note records the transport behavior confirmed while adding Dreame A2 live
video. It is aimed at maintainers and contributors working on additional mower
models or future firmware. It does not describe LAN-only streaming as a
currently supported A2 feature.

## Supported behavior

The Home Assistant camera offers two policies:

| Policy | Behavior |
| --- | --- |
| `Automatic XP2P with cached restart` | Default. Reuses previously health-checked XP2P provisioning when possible, otherwise refreshes it. Tencent still chooses the media route. |
| `XP2P (cloud provisioned)` | Always fetches current Dreame/Tencent inputs before starting normal XP2P. |

Both policies can use a direct peer route when Tencent negotiates one, but
neither promises startup without internet connectivity. The tested A2
production firmware does not advertise Tencent's separate LAN service, so the
integration does not expose a LAN-only policy.

Before trying a cached LAN or XP2P session, `Auto` performs a narrow mower
snapshot refresh and applies the same docked, returning, mapping, and
ambiguous-active-state guards as a cloud-provisioned start. It does not wait for
map hydration or runtime-metadata work. If the current state cannot be
refreshed, the camera fails closed. Cached provisioning avoids repeating
video-specific configuration calls; it does not bypass live mower safety
checks.

The integration exposes a dormant loopback FLV relay to Home Assistant. The
first actual media GET starts XP2P, while camera capability discovery remains
local. The relay owns the mower's single-consumer source and fans it out to
WebRTC, HLS, and still-image consumers. WebRTC is selected when Home Assistant
has a compatible provider; HLS remains the fallback. Once viewed, an upstream
stays warm while the mower is actively mowing so intermittent dashboard visits
do not trigger repeated cold starts. In other mower states, a 15-second
zero-viewer grace supports quick re-entry before the relay releases XP2P.

## Two different Tencent paths

The word "LAN" can refer to two different mechanisms. They should not be
treated as interchangeable.

| Normal XP2P | Separate LAN service |
| --- | --- |
| Starts through Tencent `startService` after Dreame/Tencent provisioning. | Starts through Tencent `startLanService` with a mower-advertised address and TCP port. |
| May negotiate a direct same-LAN peer or use a relay. | Is intended to connect explicitly to the mower on the local network. |
| Uses internet rendezvous/STUN during normal startup. | Depends on firmware exposing the dedicated local service. |
| A direct packet trace proves the chosen media route, not cloud-independent startup. | Requires successful LAN discovery and a health-checked local stream. |

Tencent's `getStreamLinkMode` name is also misleading for this investigation.
The observed value is a network/NAT-type bitmask, not a reliable
direct-versus-relay result. Route claims must come from socket or packet
evidence.

## Confirmed A2 findings

The following observations came from supervised tests against a production
Dreame A2:

- Python and Home Assistant captured real FLV media without an Android phone or
  Android framework. The resulting JPEG was visually inspected, the H.264 MP4
  was independently decoded, and Home Assistant HLS returned HTTP 200.
- One normal-XP2P session sent the FLV request to the mower's private IP and
  received HTTP 200 plus media bytes from that peer. This proves that normal
  XP2P can select a direct same-LAN route.
- Other sessions used Tencent network services and did not reproduce the direct
  route. Direct selection is therefore opportunistic, not controllable through
  the current API.
- The observed normal-XP2P mower port was UDP. Passing that port to
  `startLanService` made the native runtime attempt TCP and fail. A normal XP2P
  UDP port is not the dedicated LAN-service endpoint.
- Tencent's official LAN flow discovers the endpoint over UDP port 3072. The
  tested A2 firmware did not advertise a service there.
- The mower's hidden debug-camera HTTP port was closed even after the known
  debug action succeeded. Packet capture showed the requests reached the mower
  and were actively rejected, so the access point and host routing were not the
  blocker.
- Repeated tests while the mower roamed between access points confirmed that AP
  selection was not the missing ingredient. Discovery should follow the mower
  identity and current address rather than assume one fixed AP.
- Forcing the normal XP2P device configuration from TCP to UDP still produced
  video but did not force a direct private-address route.

These results explain why "direct LAN media was observed" and "LAN-only startup
works" are not equivalent statements.

## Code retained for future devices

The unsuccessful A2 firmware result did not require deleting the reusable LAN
work:

- [`lan_video.py`](../custom_components/dreame_lawn_mower/dreame_lawn_mower_client/lan_video.py)
  implements bounded UDP 3072 discovery and validates advertised endpoints.
- [`video_runtime.py`](../custom_components/dreame_lawn_mower/dreame_lawn_mower_client/video_runtime.py)
  exposes the native `startLanService` path.
- [`video_lan_cache.py`](../custom_components/dreame_lawn_mower/video_lan_cache.py)
  stores only a matching, safe endpoint after stream health succeeds.
- [`video_camera.py`](../custom_components/dreame_lawn_mower/video_camera.py)
  lets `Auto` try an advertised or previously proven LAN endpoint before normal
  XP2P fallback.
- Camera diagnostics distinguish the selected transport from Tencent's opaque
  network-type value.

This keeps the implementation ready for another mower model or a future A2
firmware release without presenting an unproved option to users.

## Validation required before enabling LAN-only mode

Do not expose a LAN-only policy for a model until a real device proves all of
the following:

1. The mower answers Tencent UDP 3072 discovery and advertises a private,
   non-loopback address plus TCP port for the same device identity.
2. `startLanService` connects to that advertised endpoint without substituting
   a normal XP2P UDP port.
3. The local HTTP source returns bytes beginning with `FLV`.
4. An independent decoder produces a real image and playable video.
5. The copied Home Assistant custom component returns HLS HTTP 200.
6. A cold Home Assistant restart succeeds while Dreame and Tencent internet
   access is blocked.
7. The connection survives or safely rediscovers the mower after it roams to a
   different access point.
8. Camera-off and unload stop every native/worker process and leave the mower in
   its expected state.

If only steps 2-5 succeed after normal XP2P provisioning, the result is useful
video proof but not cloud-independent LAN proof.

## Safety boundaries

- Do not change the Home Assistant host's IP, DNS, routes, or adapter settings
  to compensate for a mower service that is not listening.
- Do not assume a private peer address means the session can start without the
  Tencent control plane.
- Do not persist Dreame access tokens, LAN discovery tokens, or raw cloud
  responses in diagnostics or endpoint caches.
- Do not flash debug firmware or root a working mower solely to enable this
  path. A vendor-supported firmware endpoint or independently documented model
  should be used for the next proof.
