// Opt-in Lovelace example: set TARGET_ENTITY to your integration Live Video entity.
export const TARGET_ENTITY = "camera.replace_with_your_live_video_entity";
const STALL_MS = 15000;
const STARTUP_MS = 45000;

export class PlaybackWatch {
  constructor(now) {
    this.lastProgress = now;
    this.started = now;
    this.lastTime = null;
    this.lastFrames = null;
    this.hasPlayed = false;
    this.seekTried = false;
    this.retries = 0;
    this.retryAfter = 0;
    this.stableSince = now;
    this.seekingSince = null;
  }

  sample(s, now) {
    if (s.seeking) this.seekingSince ??= now;
    else this.seekingSince = null;
    const recentSeek = this.seekingSince !== null && now - this.seekingSince < STALL_MS;
    if (!s.visible || s.hidden || recentSeek || (s.paused && !s.error)) {
      this.lastProgress = now;
      this.stableSince = now;
      this.lastTime = s.time;
      this.lastFrames = s.frames;
      return null;
    }
    const clockAdvanced = this.lastTime !== null && s.time > this.lastTime + 0.02;
    const framesKnown = s.frames !== null && this.lastFrames !== null;
    const advanced = framesKnown ? s.frames > this.lastFrames : clockAdvanced;
    this.lastTime = s.time;
    this.lastFrames = s.frames;
    if (advanced) {
      this.hasPlayed = true;
      this.lastProgress = now;
      this.seekTried = false;
      if (now - this.stableSince > 30000) this.retries = 0;
      return null;
    }
    const timeout = this.hasPlayed ? STALL_MS : STARTUP_MS;
    if (now - this.lastProgress < timeout || now < this.retryAfter || this.retries >= 3) {
      return null;
    }
    this.stableSince = now;
    this.lastProgress = now;
    if (!this.seekTried && this.hasPlayed && s.liveEdge !== null && s.liveEdge > s.time + 2) {
      this.seekTried = true;
      return { type: "seek", time: Math.max(0, s.liveEdge - 1) };
    }
    this.retries += 1;
    this.retryAfter = now + 30000 * 2 ** (this.retries - 1);
    this.seekTried = false;
    this.lastTime = null;
    this.lastFrames = null;
    return { type: "restart" };
  }
}

export function restartPlayer(player) {
  // Use the element's standard disconnect/connect lifecycle, not private HA methods.
  // HA cleans up the old HLS consumer and obtains a fresh stream URL on reconnect.
  const parent = player.parentNode;
  if (!parent || !player.isConnected) return false;
  const next = player.nextSibling;
  parent.removeChild(player);
  parent.insertBefore(player, next);
  return true;
}

export function findPlayers(root, selectedEntity = null) {
  const found = [];
  const roots = [root];
  while (roots.length) {
    const current = roots.pop();
    for (const node of current.querySelectorAll("*")) {
      if (node.localName === "ha-hls-player") {
        let ancestor = node;
        let inCameraDialog = false;
        while (ancestor) {
          if (ancestor.localName === "more-info-camera") { inCameraDialog = true; break; }
          ancestor = ancestor.parentElement || ancestor.getRootNode?.().host;
        }
        // The native dialog exposes its selected entity in HA's route. This also
        // works on frontend versions that don't expose an entityid player property.
        if (node.entityid === TARGET_ENTITY || (selectedEntity === TARGET_ENTITY && inCameraDialog)) {
          found.push(node);
        }
      }
      if (node.shadowRoot) roots.push(node.shadowRoot);
    }
  }
  return found;
}

export function startRecovery(doc, win) {
  const watches = new WeakMap();
  let stopped = false;
  const tick = () => {
    if (stopped) return;
    const now = win.performance.now();
    const selectedEntity = new URLSearchParams(doc.location.search).get("more-info-entity-id");
    for (const player of findPlayers(doc, selectedEntity)) {
      const video = player.shadowRoot?.querySelector("video");
      if (!video) continue;
      let entry = watches.get(player);
      if (!entry || entry.video !== video) {
        entry = { video, watch: entry?.watch ?? new PlaybackWatch(now) };
        watches.set(player, entry);
      }
      const quality = video.getVideoPlaybackQuality?.();
      const frames = quality?.totalVideoFrames ?? video.webkitDecodedFrameCount;
      const style = win.getComputedStyle(player);
      const visible = player.isConnected && player.getClientRects().length > 0
        && style.visibility !== "hidden" && style.display !== "none";
      const action = entry.watch.sample({
        visible, hidden: doc.hidden, paused: video.paused, error: Boolean(video.error),
        seeking: video.seeking, time: video.currentTime,
        frames: Number.isFinite(frames) && frames > 0 ? frames : null,
        liveEdge: video.seekable.length ? video.seekable.end(video.seekable.length - 1) : null,
      }, now);
      if (!action) continue;
      if (action.type === "seek") {
        try { video.currentTime = action.time; } catch { /* Retry via remount on next stall. */ }
      } else {
        restartPlayer(player);
      }
    }
  };
  const interval = win.setInterval(tick, 2000);
  const stop = () => {
    stopped = true;
    win.clearInterval(interval);
    win.removeEventListener("pagehide", onPageHide);
  };
  // A bfcache page resumes its existing timers; stopping them on a persisted
  // pagehide would permanently disable recovery when the user navigates back.
  const onPageHide = (event) => { if (!event.persisted) stop(); };
  win.addEventListener("pagehide", onPageHide);
  return { stop, tick };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  // Loading the resource twice must not create two independent recovery loops.
  window.__dreameVideoRecoveryExample?.stop();
  window.__dreameVideoRecoveryExample = startRecovery(document, window);
}
