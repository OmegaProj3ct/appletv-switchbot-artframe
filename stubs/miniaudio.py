"""Stub for the 'miniaudio' package, so pyatv can be imported on a Pi Zero without compiling.

pyatv only uses miniaudio to stream audio *to* the Apple TV (RAOP). This project never
does that, so a stub is enough. Any real use raises a clear error.

Install: copy this file into the virtualenv's site-packages folder (see README).
"""
import enum

__version__ = "0.0-stub"


class DecodeError(Exception):
    pass


class MiniaudioError(Exception):
    pass


class SeekOrigin(enum.IntEnum):
    START = 0
    CURRENT = 1


class SampleFormat(enum.IntEnum):
    UNKNOWN = 0
    UNSIGNED8 = 1
    SIGNED16 = 2
    SIGNED24 = 3
    SIGNED32 = 4
    FLOAT32 = 5


class StreamableSource:
    def read(self, num_bytes):
        raise MiniaudioError("miniaudio is not installed (stub)")

    def seek(self, offset, origin):
        return False


class _Unavailable:
    def __init__(self, *a, **k):
        raise MiniaudioError("miniaudio is not installed (stub)")


class WavFileReadStream(_Unavailable):
    pass


class DecodedSoundFile(_Unavailable):
    pass


def _unavailable(*a, **k):
    raise MiniaudioError("miniaudio is not installed (stub)")


get_file_info = stream_any = decode_file = decode = stream_file = _unavailable


def __getattr__(name):
    return _unavailable
