import io
import math
import struct
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


class FakeSD(types.ModuleType):
    """Nep-sounddevice: er is hier geen geluidskaart."""

    def __init__(self):
        super().__init__("sounddevice")
        self.played = []

    def play(self, arr, sr):
        self.played.append((arr, sr))

    def wait(self):
        pass


FAKE_SD = FakeSD()
sys.modules["sounddevice"] = FAKE_SD


def make_mp3(seconds=0.5, sr=24000):
    import lameenc

    n = int(seconds * sr)
    pcm = b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(n))
    enc = lameenc.Encoder()
    enc.set_bit_rate(64)
    enc.set_in_sample_rate(sr)
    enc.set_channels(1)
    return bytes(enc.encode(pcm) + enc.flush())


@pytest.fixture
def mp3():
    return make_mp3()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def jarvis(home):
    import importlib

    import jarvis as j

    j = importlib.reload(j)
    FAKE_SD.played.clear()
    return j
