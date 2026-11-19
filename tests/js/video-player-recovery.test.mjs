import assert from "node:assert/strict";
import { test } from "node:test";
import { PlaybackWatch, restartPlayer, findPlayers, startRecovery, TARGET_ENTITY } from "../../docs/examples/video-player-recovery.js";

const sample = (overrides = {}) => ({
  visible: true, hidden: false, paused: false, seeking: false,
  time: 1, frames: 10, liveEdge: 20, ...overrides,
});
function playing() {
  const watch = new PlaybackWatch(0);
  watch.sample(sample(), 0);
  watch.sample(sample({ time: 2, frames: 20 }), 1000);
  return watch;
}
test("healthy playback never seeks or restarts", () => {
  const watch = playing();
  for (let t = 2000; t < 100000; t += 1000) {
    assert.equal(watch.sample(sample({ time: t / 1000, frames: t }), t), null);
  }
});
test("silent stall first seeks live edge, then restarts without closing dialog", () => {
  const watch = playing();
  const frozen = sample({ time: 2, frames: 20 });
  assert.deepEqual(watch.sample(frozen, 16000), { type: "seek", time: 19 });
  assert.deepEqual(watch.sample(frozen, 31000), { type: "restart" });
});
test("stalled decoded frames are detected even if the playback clock advances", () => {
  const watch = playing();
  assert.equal(watch.sample(sample({ time: 30, frames: 20 }), 16000)?.type, "restart");
});
test("without frame counters the playback clock is used", () => {
  const watch = new PlaybackWatch(0);
  watch.sample(sample({ frames: null }), 0);
  watch.sample(sample({ frames: null, time: 2 }), 1000);
  assert.equal(watch.sample(sample({ frames: null, time: 2 }), 16000)?.type, "seek");
});
for (const condition of [{ paused: true }, { hidden: true }, { visible: false }, { seeking: true }]) {
  test(`no recovery for ${JSON.stringify(condition)}`, () => {
    const watch = playing();
    assert.equal(watch.sample(sample({ time: 2, frames: 20, ...condition }), 90000), null);
  });
}
test("returning from background gets a fresh grace period", () => {
  const watch = playing();
  watch.sample(sample({ hidden: true }), 90000);
  assert.equal(watch.sample(sample(), 91000), null);
});
test("a player that never started stays untouched", () => {
  const watch = new PlaybackWatch(0);
  assert.equal(watch.sample(sample({ liveEdge: null }), 44000), null);
  assert.equal(watch.sample(sample({ liveEdge: null }), 90000), null);
});
test("retries back off and stop after three failures", () => {
  const watch = playing();
  const frozen = sample({ time: 2, frames: 20, liveEdge: null });
  assert.equal(watch.sample(frozen, 16000)?.type, "restart");
  assert.equal(watch.sample(frozen, 32000), null);
  assert.equal(watch.sample(frozen, 46000)?.type, "restart");
  assert.equal(watch.sample(frozen, 106000)?.type, "restart");
  assert.equal(watch.sample(frozen, 999000), null);
});
test("standard reconnect keeps the player node and sibling order", () => {
  const events = [];
  const sibling = {};
  const player = { isConnected: true, nextSibling: sibling };
  const parent = {
    removeChild: (node) => events.push(["disconnect", node]),
    insertBefore: (node, next) => events.push(["connect", node, next]),
  };
  player.parentNode = parent;
  assert.equal(restartPlayer(player), true);
  assert.deepEqual(events, [["disconnect", player], ["connect", player, sibling]]);
  assert.equal(restartPlayer({ isConnected: false }), false);
});
test("only the configured camera's native dialog player is selected", () => {
  const dialog = { localName: "more-info-camera" };
  const wanted = { localName: "ha-hls-player", entityid: TARGET_ENTITY, getRootNode: () => ({ host: dialog }) };
  const inline = { localName: "ha-hls-player", entityid: TARGET_ENTITY };
  const other = { localName: "ha-hls-player", entityid: "camera.other", parentElement: dialog };
  const root = { querySelectorAll: () => [inline, other, { shadowRoot: { querySelectorAll: () => [wanted] } }] };
  assert.deepEqual(findPlayers(root), [wanted]);
});
test("native dialog route scopes recovery when player entityid is absent", () => {
  const dialog = { localName: "more-info-camera" };
  const player = { localName: "ha-hls-player", parentElement: dialog };
  const unrelated = { localName: "ha-hls-player" };
  const root = { querySelectorAll: () => [player, unrelated] };
  assert.deepEqual(findPlayers(root, TARGET_ENTITY), [player]);
  assert.deepEqual(findPlayers(root, "camera.other"), []);
  assert.deepEqual(findPlayers(root), []);
});
test("a seeking event stuck forever cannot disable recovery", () => {
  const watch = playing();
  const frozen = sample({ time: 2, frames: 20, seeking: true, liveEdge: null });
  assert.equal(watch.sample(frozen, 16000), null);
  assert.equal(watch.sample(frozen, 32000)?.type, "restart");
});
test("runtime restarts only the frozen native player and survives bfcache", () => {
  let now = 0;
  let resets = 0;
  let clears = 0;
  const handlers = new Map();
  const video = { paused: false, error: null, seeking: false, currentTime: 1,
    getVideoPlaybackQuality: () => ({ totalVideoFrames: 10 }),
    seekable: { length: 0 } };
  const player = { localName: "ha-hls-player", entityid: TARGET_ENTITY, isConnected: true,
    parentElement: { localName: "more-info-camera" },
    parentNode: { removeChild: () => { resets++; }, insertBefore: () => {} },
    getClientRects: () => [1], shadowRoot: { querySelector: () => video, querySelectorAll: () => [] } };
  const doc = { hidden: false, location: { search: "" }, querySelectorAll: () => [player],
    addEventListener: (name, handler) => handlers.set(name, handler),
    removeEventListener: name => handlers.delete(name) };
  const win = { performance: { now: () => now }, setInterval: () => 1,
    clearInterval: () => { clears++; }, getComputedStyle: () => ({ visibility: "visible", display: "block" }),
    addEventListener: (name, handler) => handlers.set(name, handler),
    removeEventListener: (name) => handlers.delete(name) };
  const recovery = startRecovery(doc, win);
  recovery.tick();
  now = 1000; video.currentTime = 2;
  video.getVideoPlaybackQuality = () => ({ totalVideoFrames: 20 });
  recovery.tick();
  now = 16000; recovery.tick();
  assert.equal(resets, 1);
  handlers.get("pagehide")({ persisted: true });
  assert.equal(clears, 0);
  handlers.get("pagehide")({ persisted: false });
  assert.equal(clears, 1);
  now = 999000; recovery.tick();
  assert.equal(resets, 1);
});

test("a seek jump without subsequent playback cannot become repeated seeks", () => {
  const watch = new PlaybackWatch(0);
  watch.sample(sample({ frames: null }), 0);
  watch.sample(sample({ frames: null, time: 2 }), 1000);
  assert.equal(watch.sample(sample({ frames: null, time: 2 }), 16000)?.type, "seek");
  assert.equal(watch.sample(sample({ frames: null, time: 19.2, liveEdge: 30 }), 18000), null);
  assert.equal(watch.sample(sample({ frames: null, time: 19.2, liveEdge: 40 }), 31000)?.type, "restart");
});
function exhausted() {
  const watch = playing();
  const frozen = sample({ time: 2, frames: 20, liveEdge: null });
  for (const now of [16000, 46000, 106000]) watch.sample(frozen, now);
  watch.sample(frozen, 108000);
  return watch;
}
test("one late frame cannot reset an exhausted retry budget", () => {
  const watch = exhausted();
  watch.sample(sample({ time: 3, frames: 21, liveEdge: null }), 138000);
  assert.equal(watch.sample(sample({ time: 3, frames: 21, liveEdge: null }), 154000), null);
  assert.equal(watch.retries, 3);
});
test("continuous recovery clears the obsolete backoff deadline", () => {
  const watch = exhausted();
  for (let now = 108000; now <= 140000; now += 2000) {
    watch.sample(sample({ time: now / 1000, frames: now, liveEdge: null }), now);
  }
  assert.equal(watch.retries, 0);
  assert.equal(watch.retryAfter, 0);
  assert.equal(watch.sample(sample({ time: 140, frames: 140000, liveEdge: null }), 156000)?.type, "restart");
});
test("an explicit other entity wins over the target dialog route", () => {
  const dialog = { localName: "more-info-camera" };
  const other = { localName: "ha-hls-player", entityid: "camera.other", parentElement: dialog };
  assert.deepEqual(findPlayers({ querySelectorAll: () => [other] }, TARGET_ENTITY), []);
});

function runtimeHarness() {
  let now = 0, resets = 0;
  const handlers = new Map();
  const video = () => ({ paused: false, error: null, seeking: false, currentTime: 1,
    total: 10, dropped: 0, seekable: { length: 0 },
    getVideoPlaybackQuality() { return { totalVideoFrames: this.total, droppedVideoFrames: this.dropped }; } });
  let media = video();
  const player = { localName: "ha-hls-player", entityid: TARGET_ENTITY, isConnected: true,
    parentElement: { localName: "more-info-camera" },
    parentNode: { removeChild: () => { resets++; }, insertBefore: () => {} },
    getClientRects: () => [1], shadowRoot: { querySelector: () => media, querySelectorAll: () => [] } };
  const doc = { hidden: false, location: { search: "" }, querySelectorAll: () => [player],
    addEventListener: (name, handler) => handlers.set(name, handler),
    removeEventListener: name => handlers.delete(name) };
  const win = { performance: { now: () => now }, setInterval: () => 1, clearInterval: () => {},
    getComputedStyle: () => ({ visibility: "visible", display: "block" }),
    addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name) };
  const recovery = startRecovery(doc, win);
  return { tick: time => { now = time; recovery.tick(); }, doc, handlers,
    get media() { return media; }, set media(value) { media = value; },
    get resets() { return resets; }, video, stop: recovery.stop };
}
test("runtime does not mistake dropped-only frames for displayed playback", () => {
  const h = runtimeHarness(); h.tick(0);
  h.media.currentTime = 2; h.media.total = 20; h.media.dropped = 0; h.tick(1000);
  h.media.currentTime = 30; h.media.total = 40; h.media.dropped = 20; h.tick(16000);
  assert.equal(h.resets, 1); h.stop();
});
test("zero displayed frames remain a usable counter", () => {
  const h = runtimeHarness();
  h.media.total = h.media.dropped = 10; h.tick(0);
  h.media.currentTime = 20; h.media.total = h.media.dropped = 30; h.tick(20000);
  h.media.currentTime = 50; h.media.total = h.media.dropped = 60; h.tick(45000);
  assert.equal(h.resets, 0); h.stop();
});
test("a replacement video gets a fresh playback baseline and keeps the retry budget", () => {
  const h = runtimeHarness();
  for (const [attempt, start] of [0, 60000, 140000, 220000].entries()) {
    h.media = h.video(); h.tick(start);
    h.tick(start + 20000);
    assert.equal(h.resets, attempt);
    h.media.currentTime = 2; h.media.total = 20; h.tick(start + 21000);
    h.tick(start + 36000);
  }
  assert.equal(h.resets, 3); h.stop();
});
test("bfcache return gets a new baseline before the first resumed frame", () => {
  const h = runtimeHarness(); h.tick(0);
  h.media.currentTime = 2; h.media.total = 20; h.tick(1000);
  h.handlers.get('pagehide')({ persisted: true });
  h.handlers.get('pageshow')?.({ persisted: true });
  h.tick(90000);
  assert.equal(h.resets, 0); h.stop();
});
test("return from a hidden tab gets a fresh stall timer even without background ticks", () => {
  const h = runtimeHarness(); h.tick(0);
  h.media.currentTime = 2; h.media.total = 20; h.tick(1000);
  h.doc.hidden = true; h.handlers.get("visibilitychange")?.();
  h.doc.hidden = false; h.handlers.get("visibilitychange")?.();
  h.tick(90000);
  assert.equal(h.resets, 0);
  h.tick(105000);
  assert.equal(h.resets, 1);
  h.stop();
  assert.equal(h.handlers.has("visibilitychange"), false);
});
test("a paused player stays paused even if the media element has an error", () => {
  const h = runtimeHarness(); h.tick(0);
  h.media.currentTime = 2; h.media.total = 20; h.tick(1000);
  h.media.paused = true; h.media.error = { code: 3 }; h.tick(90000);
  assert.equal(h.resets, 0);
  h.media.paused = false; h.tick(92000);
  h.tick(105000);
  assert.equal(h.resets, 1); h.stop();
});
