"""A framed control failure must not leave a reusable, ambiguous pipe."""

from __future__ import annotations

import io
import struct
import threading

import pytest

from custom_components.dreame_lawn_mower.dreame_lawn_mower_client.video_runtime import (
    DreameLawnMowerVideoRuntimeError,
)

from .test_xp2p_talk import _runtime, _session


class _Process:
    def __init__(self, stdout, *, blocked_write=False):
        self.released = threading.Event()
        self.entered = threading.Event()
        self.exited = False
        self.stdout = stdout
        self.stdin = io.BytesIO()
        if blocked_write:
            self.stdin = self

    def write(self, _data):
        self.entered.set()
        assert self.released.wait(2)

    def flush(self):
        pass

    def read(self, _size):
        self.entered.set()
        assert self.released.wait(2)
        return b""

    def poll(self):
        return 0 if self.exited else None

    def terminate(self):
        self.exited = True
        self.released.set()

    def wait(self, *, timeout):
        return 0


@pytest.mark.parametrize("operation", ["talk", "route"])
@pytest.mark.parametrize("response", ["timeout", "magic", "oversized", "blocked_write"])
def test_control_failure_retires_worker_before_any_following_request(
    tmp_path, operation, response
):
    process = _Process(io.BytesIO(), blocked_write=response == "blocked_write")
    if response == "timeout":
        process.stdout = process
    elif response == "magic":
        process.stdout = io.BytesIO(b"BAD!" + struct.pack("!II", 0, 0))
    elif response == "oversized":
        process.stdout = io.BytesIO(b"DXR1" + struct.pack("!II", 0, 0xFFFFFFFF))
    session = _session(process)
    runtime = _runtime(tmp_path)
    try:
        if operation == "route":
            assert runtime.refresh_stream_link_mode(session, timeout=0.1) is None
        else:
            with pytest.raises(DreameLawnMowerVideoRuntimeError):
                runtime.start_talk(session, timeout=0.1)
        assert process.exited, (
            "a failed frame cannot safely share pipes with another request"
        )
        with pytest.raises(DreameLawnMowerVideoRuntimeError, match="not running"):
            runtime.stop_talk(session, timeout=0.1)
        runtime.stop_live_stream(session)
    finally:
        process.released.set()


def test_stop_live_stream_terminates_even_when_a_control_writer_owns_the_pipe(tmp_path):
    process = _Process(io.BytesIO(), blocked_write=True)
    session = _session(process)
    runtime = _runtime(tmp_path)
    finished = threading.Event()
    errors = []

    def control():
        try:
            runtime.start_talk(session, timeout=0.1)
        except DreameLawnMowerVideoRuntimeError:
            pass
        except Exception as err:
            errors.append(err)

    def stop():
        try:
            runtime.stop_live_stream(session)
        except Exception as err:
            errors.append(err)
        finally:
            finished.set()

    writer = threading.Thread(target=control, daemon=True)
    stopper = threading.Thread(target=stop, daemon=True)
    writer.start()
    try:
        assert process.entered.wait(1)
        stopper.start()
        assert finished.wait(1), (
            "shutdown must not block on the abandoned buffered writer"
        )
        assert process.exited
        assert not errors
    finally:
        process.released.set()
        writer.join(2)
        if stopper.ident is not None:
            stopper.join(2)
