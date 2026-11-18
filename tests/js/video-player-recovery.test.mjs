import assert from "node:assert/strict";
import { test } from "node:test";
import { PlaybackWatch, restartPlayer, findPlayers, startRecovery, TARGET_ENTITY } from "../../docs/examples/video-player-recovery.js";

const sample = (overrides = {}) => ({
  visible: true, hidden: false, paused: false, error: false, seeking: false,
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
test("initial connection has 45 seconds to produce media", () => {
  const watch = new PlaybackWatch(0);
  assert.equal(watch.sample(sample({ liveEdge: null }), 44000), null);
  assert.equal(watch.sample(sample({ liveEdge: null }), 45000)?.type, "restart");
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
test("only the exact MOVA live entity is selected through open shadow roots", () => {
  const wanted = { localName: "ha-hls-player", entityid: TARGET_ENTITY };
  const other = { localName: "ha-hls-player", entityid: "camera.other" };
  const root = { querySelectorAll: () => [other, { shadowRoot: { querySelectorAll: () => [wanted] } }] };
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
    parentNode: { removeChild: () => { resets++; }, insertBefore: () => {} },
    getClientRects: () => [1], shadowRoot: { querySelector: () => video, querySelectorAll: () => [] } };
  const doc = { hidden: false, location: { search: "" }, querySelectorAll: () => [player] };
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
