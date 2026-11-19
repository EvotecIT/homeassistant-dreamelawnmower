// Opt-in Lovelace example: set TARGET_ENTITY to your integration Live Video entity.
export const TARGET_ENTITY = "camera.replace_with_your_live_video_entity";
const STALL_MS = 15000;

export class PlaybackWatch {
  constructor(now, budget = {}) {
    this.lastProgress = now;
    this.lastTime = null;
    this.lastFrames = null;
    this.hasPlayed = false;
    this.seekTried = false;
    this.retries = budget.retries ?? 0;
    this.retryAfter = budget.retryAfter ?? 0;
    this.stableSince = null;
    this.seekingSince = null;
    this.seekBaselinePending = false;
  }

  resume(now) {
    this.lastProgress = now;
    this.lastTime = null;
    this.lastFrames = null;
    this.stableSince = null;
    this.seekingSince = null;
    this.seekBaselinePending = false;
  }

  sample(s, now) {
    if (s.seeking) this.seekingSince ??= now;
    else this.seekingSince = null;
    const recentSeek = this.seekingSince !== null && now - this.seekingSince < STALL_MS;
    if (!s.visible || s.hidden || s.paused || recentSeek) {
      this.lastProgress = now;
      this.stableSince = null;
      this.lastTime = s.time;
      this.lastFrames = s.frames;
      return null;
    }
    const clockAdvanced = this.lastTime !== null && s.time > this.lastTime + 0.02;
    const framesKnown = s.frames !== null && this.lastFrames !== null;
    const seekBaseline = this.seekBaselinePending;
    this.seekBaselinePending = false;
    const advanced = !seekBaseline && (framesKnown ? s.frames > this.lastFrames : clockAdvanced);
    this.lastTime = s.time;
    this.lastFrames = s.frames;
    if (advanced) {
      this.hasPlayed = true;
      this.lastProgress = now;
      this.seekTried = false;
      this.stableSince ??= now;
      if (now - this.stableSince >= 30000) {
        this.retries = 0;
        this.retryAfter = 0;
      }
      return null;
    }
    this.stableSince = null;
    if (!this.hasPlayed || now - this.lastProgress < STALL_MS || now < this.retryAfter || this.retries >= 3) {
      return null;
    }
    this.lastProgress = now;
    if (!this.seekTried && s.liveEdge !== null && s.liveEdge > s.time + 2) {
      this.seekTried = true;
      this.seekBaselinePending = true;
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
  // HA 2026.11.2 cleans up the old consumer and reloads its existing stream URL.
  // This does not promise to renew an expired URL.
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
        const entity = node.entityid;
        if (inCameraDialog && (entity || selectedEntity) === TARGET_ENTITY) {
          found.push(node);
        }
      }
      if (node.shadowRoot) roots.push(node.shadowRoot);
    }
  }
  return found;
}

export function startRecovery(doc, win) {
  const watches = new Map();
  let stopped = false;
  let resumeGeneration = 0;
  const tick = () => {
    if (stopped) return;
    const now = win.performance.now();
    const selectedEntity = new URLSearchParams(doc.location.search).get("more-info-entity-id");
    const players = findPlayers(doc, selectedEntity);
    const active = new Set(players);
    for (const player of watches.keys()) {
      if (!active.has(player)) watches.delete(player);
    }
    for (const player of players) {
      const video = player.shadowRoot?.querySelector("video");
      if (!video) continue;
      let entry = watches.get(player);
      if (!entry || entry.video !== video) {
        entry = { video, watch: new PlaybackWatch(now, entry?.watch), generation: resumeGeneration };
        watches.set(player, entry);
      }
      if (entry.generation !== resumeGeneration) {
        entry.watch.resume(now);
        entry.generation = resumeGeneration;
      }
      const quality = video.getVideoPlaybackQuality?.();
      const frames = Number.isFinite(quality?.totalVideoFrames) && Number.isFinite(quality?.droppedVideoFrames)
        ? Math.max(0, quality.totalVideoFrames - quality.droppedVideoFrames)
        : video.webkitDecodedFrameCount;
      const style = win.getComputedStyle(player);
      const visible = player.isConnected && player.getClientRects().length > 0
        && style.visibility !== "hidden" && style.display !== "none";
      const action = entry.watch.sample({
        visible, hidden: doc.hidden, paused: video.paused,
        seeking: video.seeking, time: video.currentTime,
        frames: Number.isFinite(frames) && frames >= 0 ? frames : null,
        liveEdge: video.seekable.length ? video.seekable.end(video.seekable.length - 1) : null,
      }, now);
      if (!action) continue;
      if (action.type === "seek") {
        try { video.currentTime = action.time; } catch { /* Retry via remount on next stall. */ }
      } else {
        restartPlayer(player);
        // Wait for progress on the reconnected media while keeping this retry budget.
        entry.video = null;
      }
    }
  };
  const interval = win.setInterval(tick, 2000);
  const stop = () => {
    stopped = true;
    win.clearInterval(interval);
    win.removeEventListener("pagehide", onPageHide);
    win.removeEventListener("pageshow", onPageShow);
    watches.clear();
  };
  // A bfcache page resumes its existing timers; stopping them on a persisted
  // pagehide would permanently disable recovery when the user navigates back.
  const onPageHide = (event) => { if (!event.persisted) stop(); };
  const onPageShow = (event) => { if (event.persisted) resumeGeneration++; };
  win.addEventListener("pagehide", onPageHide);
  win.addEventListener("pageshow", onPageShow);
  return { stop, tick };
}

if (typeof window !== "undefined" && typeof document !== "undefined") {
  // Loading the resource twice must not create two independent recovery loops.
  window.__dreameVideoRecoveryExample?.stop();
  window.__dreameVideoRecoveryExample = startRecovery(document, window);
}
