import json
import os
import re
import stat
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from conftest import FAKE_SD

# ------------------------------------------------------------------ hulpjes


def cfg_for(j, home, **extra):
    c = json.loads(json.dumps(j.DEFAULT_CONFIG))
    c["werkmap"] = str(home / "werk")
    os.makedirs(c["werkmap"], exist_ok=True)
    for k, v in extra.items():
        c[k] = v
    return c


TWILIO = {"account_sid": "AC" + "0" * 32, "auth_token": "tok",
          "twilio_nummer": "+12025550123", "jouw_nummer": "06 1234 5678"}


# ------------------------------------------------------------------ tekst


def test_spoken_removes_markdown_and_links(jarvis):
    t = "# Klaar\n**Website** gemaakt in `index.html`.\n- punt een\n```py\nprint(1)\n```\nZie https://x.nl/a en [docs](https://y)."
    out = jarvis.spoken(t)
    assert "**" not in out and "`" not in out and "https" not in out and "print" not in out
    assert "Website gemaakt in index.html" in out and "een link" in out and "docs" in out


def test_spoken_limits_length_on_sentence(jarvis):
    out = jarvis.spoken("Dit is een zin. " * 200)
    assert len(out) <= 900 and out.endswith(".")


def test_spoken_empty(jarvis):
    assert jarvis.spoken("```x```") == "Klaar."


@pytest.mark.parametrize("text,cmd", [
    ("Jarvis, maak een website.", "maak een website."),
    ("Hé Jarvis zet de muziek aan", "zet de muziek aan"),
    ("jervis! wat is het weer", "wat is het weer"),
    ("Javis, bel me", "bel me"),
])
def test_wake_word(jarvis, text, cmd):
    m = jarvis.WAKE.search(text)
    assert m and text[m.end():].strip() == cmd


def test_wake_word_not_in_normal_speech(jarvis):
    assert not jarvis.WAKE.search("ik ga even naar de winkel")


@pytest.mark.parametrize("raw,norm", [
    ("06 1234 5678", "+31612345678"), ("+31 6-12345678", "+31612345678"),
    ("0031612345678", "+31612345678"), ("+1 (202) 555-0123", "+12025550123"),
])
def test_norm_nr(jarvis, raw, norm):
    assert jarvis.norm_nr(raw) == norm


# ------------------------------------------------------------------ config


def test_config_merge_and_home(jarvis, home):
    (home / "config.json").write_text(json.dumps({
        "aanspreekvorm": "baas", "werkmap": str(home / "w"),
        "twilio": {"jouw_nummer": "0612345678"},
    }), encoding="utf-8")
    c = jarvis.load_config()
    assert jarvis.CONFIG_PATH == home / "config.json"
    assert c["aanspreekvorm"] == "baas"
    assert c["twilio"]["jouw_nummer"] == "0612345678" and c["twilio"]["auth_token"] == ""
    assert c["elevenlabs"]["gebruik"] == "telefoon"  # standaard alleen telefoon
    assert (home / "w").is_dir()


# ------------------------------------------------------------------ brein (nep-claude)

FAKE_CLAUDE = r'''#!{py}
import json, sys, os
log = os.environ["FAKE_LOG"]
args = sys.argv[1:]
with open(log, "a") as f: f.write(json.dumps({{"args": args, "cwd": os.getcwd()}}) + "\n")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "garbage": print("Error: not logged in"); sys.exit(1)
if "--resume" in args and mode == "badresume": print("No conversation found"); sys.exit(1)
if mode == "error": print(json.dumps({{"is_error": True, "result": "kapot", "session_id": "s1"}})); sys.exit(0)
print("some log line")
print(json.dumps({{"type": "result", "is_error": False, "result": "Gedaan: " + args[1], "session_id": "sess-123"}}))
'''


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    p = tmp_path / "claude"
    p.write_text(FAKE_CLAUDE.format(py=sys.executable))
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    logf = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(logf))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    return lambda: [json.loads(line) for line in logf.read_text().splitlines()]


def test_brain_flags_session_and_resume(jarvis, home, fake_claude):
    b = jarvis.Brain(cfg_for(jarvis, home))
    assert b.run("maak een map") == "Gedaan: maak een map"
    assert b.run("en nog een") == "Gedaan: en nog een"
    calls = fake_claude()
    a1, a2 = calls[0]["args"], calls[1]["args"]
    assert a1[:2] == ["-p", "maak een map"]
    assert "--output-format" in a1 and "json" in a1
    assert "--append-system-prompt" in a1 and "JARVIS" in a1[a1.index("--append-system-prompt") + 1]
    assert a1[a1.index("--permission-mode") + 1] == "acceptEdits"
    assert "--resume" not in a1
    assert a2[a2.index("--resume") + 1] == "sess-123"
    assert calls[0]["cwd"] == str(home / "werk")
    assert json.loads((home / "state.json").read_text())["session_id"] == "sess-123"


def test_brain_full_mode(jarvis, home, fake_claude):
    c = cfg_for(jarvis, home, veilige_modus=False)
    jarvis.Brain(c).run("x")
    a = fake_claude()[0]["args"]
    assert "--dangerously-skip-permissions" in a and "--permission-mode" not in a


def test_brain_bad_resume_starts_fresh(jarvis, home, fake_claude, monkeypatch):
    b = jarvis.Brain(cfg_for(jarvis, home))
    b.run("eerste")
    monkeypatch.setenv("FAKE_MODE", "badresume")
    assert b.run("tweede") == "Gedaan: tweede"
    calls = fake_claude()
    assert "--resume" in calls[1]["args"] and "--resume" not in calls[2]["args"]


def test_brain_errors(jarvis, home, fake_claude, monkeypatch):
    b = jarvis.Brain(cfg_for(jarvis, home))
    monkeypatch.setenv("FAKE_MODE", "error")
    assert "kapot" in b.run("x")
    b.reset()
    monkeypatch.setenv("FAKE_MODE", "garbage")
    assert "ingelogd" in b.run("x")


def test_brain_missing_claude(jarvis, home):
    b = jarvis.Brain(cfg_for(jarvis, home))
    b.claude = str(home / "bestaat-niet.exe")
    assert "niet vinden" in b.run("x")


# ------------------------------------------------------------------ stem


def test_voice_local_uses_free_voice_by_default(jarvis, home, mp3, monkeypatch):
    c = cfg_for(jarvis, home)
    c["elevenlabs"]["api_key"] = "sk"
    v = jarvis.Voice(c)
    used = []
    monkeypatch.setattr(v.eleven, "tts", lambda t: used.append(t) or mp3)

    async def synth(t):
        return mp3
    monkeypatch.setattr(v, "_synth", synth)
    v.say("**Hallo** meneer")
    assert used == []  # ElevenLabs niet gebruikt aan de pc
    arr, sr = FAKE_SD.played[-1]
    assert sr == 24000 and arr.dtype == np.int16 and len(arr) > 5000
    assert not v.speaking.is_set()


def test_voice_local_eleven_when_overal(jarvis, home, mp3, monkeypatch):
    c = cfg_for(jarvis, home)
    c["elevenlabs"].update(api_key="sk", gebruik="overal")
    v = jarvis.Voice(c)
    used = []
    monkeypatch.setattr(v.eleven, "tts", lambda t: used.append(t) or mp3)
    v.say("Hallo")
    assert used == ["Hallo"] and FAKE_SD.played


def test_voice_survives_errors(jarvis, home, monkeypatch):
    v = jarvis.Voice(cfg_for(jarvis, home))

    async def boom(t):
        raise RuntimeError("geen internet")
    monkeypatch.setattr(v, "_synth", boom)
    v.say("hoi")  # mag niet crashen
    assert not v.speaking.is_set()


# ------------------------------------------------------------------ ElevenLabs (lokale nepserver)


class ElevenFake(BaseHTTPRequestHandler):
    mode = "ok"
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        ElevenFake.seen.append(("GET", self.path, dict(self.headers), None))
        self._send(200, json.dumps({"voices": [
            {"voice_id": "F1", "name": "Sarah", "category": "premade", "labels": {"gender": "female"}},
            {"voice_id": "M1", "name": "Brian", "category": "premade", "labels": {"gender": "male"}},
        ]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        ElevenFake.seen.append(("POST", self.path, dict(self.headers), body))
        if ElevenFake.mode == "quota":
            return self._send(401, b'{"detail":{"status":"quota_exceeded","message":"You exceeded your quota"}}')
        if ElevenFake.mode == "library" and "/LIB" in self.path:
            return self._send(402, b'{"detail":{"status":"payment_required","message":"Free users cannot use library voices via the API"}}')
        self._send(200, b"MP3DATA", "audio/mpeg")


@pytest.fixture
def eleven_srv(jarvis, monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), ElevenFake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(jarvis.ElevenLabs, "API", f"http://127.0.0.1:{srv.server_port}/v1")
    ElevenFake.seen.clear()
    yield ElevenFake
    srv.shutdown()


def test_eleven_request_format(jarvis, home, eleven_srv):
    c = cfg_for(jarvis, home)
    c["elevenlabs"].update(api_key="sk_abc", voice_id="V1")
    e = jarvis.ElevenLabs(c)
    eleven_srv.mode = "ok"
    assert e.tts("Goedendag") == b"MP3DATA"
    m, path, headers, body = eleven_srv.seen[-1]
    assert m == "POST" and path == "/v1/text-to-speech/V1?output_format=mp3_44100_128"
    assert {k.lower(): v for k, v in headers.items()}["xi-api-key"] == "sk_abc"
    assert body == {"text": "Goedendag", "model_id": "eleven_flash_v2_5", "language_code": "nl"}
    e.tts("Goedendag")  # korte zinnen uit cache
    assert len(eleven_srv.seen) == 1


def test_eleven_library_voice_falls_back_to_premade_male(jarvis, home, eleven_srv):
    c = cfg_for(jarvis, home)
    c["elevenlabs"].update(api_key="sk", voice_id="LIB")
    e = jarvis.ElevenLabs(c)
    eleven_srv.mode = "library"
    assert e.tts("Hallo") == b"MP3DATA"
    assert e.voice_id == "M1"


def test_eleven_quota_pauses_and_falls_back(jarvis, home, eleven_srv):
    c = cfg_for(jarvis, home)
    c["elevenlabs"].update(api_key="sk")
    e = jarvis.ElevenLabs(c)
    eleven_srv.mode = "quota"
    assert e.tts("Hallo") is None and not e.enabled
    n = len(eleven_srv.seen)
    assert e.tts("Nog iets") is None and len(eleven_srv.seen) == n  # geen nieuwe pogingen


def test_eleven_off_without_key_and_unreachable(jarvis, home, monkeypatch):
    c = cfg_for(jarvis, home)
    assert jarvis.ElevenLabs(c).tts("x") is None
    c["elevenlabs"]["api_key"] = "sk"
    monkeypatch.setattr(jarvis.ElevenLabs, "API", "http://127.0.0.1:9/v1")
    assert jarvis.ElevenLabs(c).tts("x") is None


# ------------------------------------------------------------------ oren (microfoon-logica)


class FakeVoice:
    def __init__(self):
        self.said = []
        import threading as t
        self.speaking = t.Event()

    def say(self, text):
        self.said.append(text)


def make_ears(jarvis, home, texts):
    c = cfg_for(jarvis, home)
    cmds = []
    jarvis.Ears._load_model = lambda self: None
    voice = FakeVoice()
    ears = jarvis.Ears(c, voice, cmds.append)
    heard = []

    def transcribe(audio):
        heard.append(len(audio) / 16000)
        return texts.pop(0)
    ears.transcribe = transcribe
    return ears, voice, cmds, heard


def speech(ears, seconds, loud=True):
    for _ in range(int(seconds / 0.03)):
        amp = 0.2 if loud else 0.001
        ears.feed((amp * np.sin(np.arange(480) / 3)).astype(np.float32))


def test_ears_command_in_one_sentence(jarvis, home):
    ears, voice, cmds, heard = make_ears(jarvis, home, ["Jarvis, maak een website over honden."])
    speech(ears, 0.5, False)
    speech(ears, 2.0)
    speech(ears, 1.0, False)
    assert cmds == ["maak een website over honden."]
    assert 2.0 < heard[0] < 3.3  # zin + voorloop + stilte


def test_ears_wake_then_command(jarvis, home):
    ears, voice, cmds, _ = make_ears(jarvis, home, ["Jarvis.", "Wat staat er in mijn agenda?"])
    speech(ears, 0.6); speech(ears, 1.0, False)
    assert voice.said == ["Ja, meneer?"] and cmds == []
    speech(ears, 1.5); speech(ears, 1.0, False)
    assert cmds == ["Wat staat er in mijn agenda?"]


def test_ears_wake_window_expires(jarvis, home):
    ears, voice, cmds, _ = make_ears(jarvis, home, ["Jarvis.", "zomaar wat gepraat"])
    speech(ears, 0.6); speech(ears, 1.0, False)
    ears.awaiting_until = time.time() - 1
    speech(ears, 1.0); speech(ears, 1.0, False)
    assert cmds == []


def test_ears_ignores_other_speech_and_clicks(jarvis, home):
    ears, voice, cmds, heard = make_ears(jarvis, home, ["Ik ga naar de winkel."])
    speech(ears, 0.1); speech(ears, 1.0, False)  # klik: te kort
    assert heard == []
    speech(ears, 1.5); speech(ears, 1.0, False)
    assert cmds == [] and voice.said == [] and len(heard) == 1


def test_ears_max_length(jarvis, home):
    ears, _, cmds, heard = make_ears(jarvis, home, ["Jarvis doe iets"])
    speech(ears, 31)
    assert heard and heard[0] <= 30.5 and cmds == ["doe iets"]


def test_ears_mute_while_speaking(jarvis, home):
    ears, voice, _, _ = make_ears(jarvis, home, [])
    voice.speaking.set()
    ears._callback(np.ones((480, 1), np.float32), 480, None, None)
    assert ears.q.empty()
    voice.speaking.clear()
    ears._callback(np.ones((480, 1), np.float32), 480, None, None)
    assert ears.q.qsize() == 1


# ------------------------------------------------------------------ telefoon


class FakeBrain:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset_called = False

    def busy(self):
        return self.lock.locked()

    def reset(self):
        self.reset_called = True

    def run(self, text):
        with self.lock:
            m = re.search(r"([\d.]+)$", text)
            time.sleep(float(m.group(1)) if m else 0)
            return f"**Klaar** met `{text}`. Zie https://x.nl"


@pytest.fixture
def phone(jarvis, home, mp3):
    from twilio.request_validator import RequestValidator

    c = cfg_for(jarvis, home, twilio=dict(TWILIO))
    c["elevenlabs"]["api_key"] = "sk"
    voice = jarvis.Voice(c)
    voice.eleven.tts = lambda t: mp3
    ph = jarvis.Phone(c, FakeBrain(), voice)
    ph.base = "https://abc.trycloudflare.com"
    ph.calls = []
    ph.call = lambda p: ph.calls.append(p)
    client = ph.app.test_client()
    val = RequestValidator("tok")

    def post(path, sign=True, **form):
        form.setdefault("From", "+31612345678")
        form.setdefault("To", "+12025550123")
        h = {"X-Twilio-Signature": val.compute_signature(ph.base + path, form)} if sign else {}
        r = client.post(path, data=form, headers=h)
        return r.status_code, r.data.decode()

    ph.post, ph.client = post, client
    return ph


def urls(xml, kind):
    return [u.replace("&amp;", "&") for u in re.findall(rf"<{kind}[^>]*>([^<]+)</{kind}>", xml)]


def test_phone_security(phone):
    assert phone.post("/voice/in", sign=False)[0] == 403
    assert phone.post("/voice/in", From="+31699999999")[0] == 403
    code, xml = phone.post("/voice/in")
    assert code == 200 and "<Gather" in xml and 'language="nl-NL"' in xml


def test_phone_plays_elevenlabs_audio(phone, mp3):
    _, xml = phone.post("/voice/in")
    plays = urls(xml, "Play")
    assert plays and "<Say" not in xml
    path = plays[0].replace(phone.base, "")
    r = phone.client.get(path)  # Twilio haalt audio op zonder handtekening
    assert r.status_code == 200 and r.mimetype == "audio/mpeg" and r.data == mp3
    assert phone.client.get("/audio/onbekend.mp3").status_code == 404


def test_phone_falls_back_to_polly(phone):
    phone.voice.eleven.tts = lambda t: None
    _, xml = phone.post("/voice/in")
    assert 'voice="Polly.Ruben"' in xml and "Waarmee kan ik helpen" in xml


def test_phone_quick_task_answered_in_call(phone):
    phone.voice.eleven.tts = lambda t: None
    _, xml = phone.post("/voice/heard", SpeechResult="snel 0.1")
    wait = urls(xml, "Redirect")[0].replace(phone.base, "")
    time.sleep(0.4)
    _, xml = phone.post(wait)
    assert "Klaar met snel 0.1. Zie een link" in xml and "Kan ik nog iets" in xml
    assert phone.calls == []


def test_phone_wait_loop_then_callback(phone):
    phone.voice.eleven.tts = lambda t: None
    _, xml = phone.post("/voice/heard", SpeechResult="lang 1")
    wait = urls(xml, "Redirect")[0].replace(phone.base, "")
    _, xml = phone.post(wait)
    assert "<Pause" in xml and "n=1" in urls(xml, "Redirect")[0]
    _, xml = phone.post(wait.replace("n=0", "n=8"))
    assert "ik bel u terug" in xml.lower() and "<Hangup" in xml
    time.sleep(1.4)
    assert len(phone.calls) == 1 and phone.calls[0].startswith("/voice/result?job=")
    _, xml = phone.post(phone.calls[0], From="+12025550123", To="+31612345678")  # uitgaand gesprek
    assert "Jarvis hier" in xml and "Klaar met lang 1" in xml and "<Gather" in xml


def test_phone_goodbye_and_silence(phone):
    phone.voice.eleven.tts = lambda t: None
    for s in ("Nee dank je, doei", ""):
        _, xml = phone.post("/voice/heard", SpeechResult=s)
        assert "Tot uw dienst" in xml and "<Hangup" in xml


def test_phone_notify_message(phone):
    phone.voice.eleven.tts = lambda t: None
    phone.notify("De **website** is klaar.")
    path = phone.calls[0]
    _, xml = phone.post(path, From="+12025550123", To="+31612345678")
    assert "De website is klaar." in xml


def test_phone_start_tunnel_and_number_hookup(jarvis, home, monkeypatch):
    fake = home / "bin" / "cloudflared"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\nimport time,sys\n"
                    "print('INF Requesting new quick Tunnel', flush=True)\n"
                    "print('INF |  https://happy-fox-12.trycloudflare.com  |', flush=True)\n"
                    "time.sleep(30)\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setattr(jarvis.time, "sleep", lambda s: None)
    c = cfg_for(jarvis, home, twilio=dict(TWILIO), poort=18765)
    ph = jarvis.Phone(c, FakeBrain(), None)
    updated = {}

    class Num:
        def update(self, **kw):
            updated.update(kw)

    class Lst:
        def list(self, phone_number):
            updated["nr"] = phone_number
            return [Num()]
    ph.client = type("C", (), {"incoming_phone_numbers": Lst()})()
    ph.start()
    try:
        assert ph.base == "https://happy-fox-12.trycloudflare.com"
        assert updated == {"nr": "+12025550123", "voice_url": ph.base + "/voice/in", "voice_method": "POST"}
    finally:
        ph.tunnel.kill()


def test_phone_call_uses_twilio_correctly(jarvis, home, monkeypatch):
    c = cfg_for(jarvis, home, twilio=dict(TWILIO))
    ph = jarvis.Phone(c, FakeBrain(), None)
    ph.base = "https://t.trycloudflare.com"
    got = {}

    class Calls:
        def create(self, **kw):
            got.update(kw)
    ph.client = type("C", (), {"calls": Calls()})()
    ph.call("/voice/msg?id=1")
    assert got == {"to": "+31612345678", "from_": "+12025550123",
                   "url": "https://t.trycloudflare.com/voice/msg?id=1", "method": "POST"}


# ------------------------------------------------------------------ hoofdprogramma


@pytest.fixture
def jv(jarvis, home):
    j = jarvis.Jarvis.__new__(jarvis.Jarvis)
    j.cfg = cfg_for(jarvis, home)
    j.brain = FakeBrain()
    j.voice = FakeVoice()
    j.phone = None
    return j


def wait_threads():
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon and t.name.startswith("Thread"):
            t.join(timeout=3)


def test_command_runs_task_and_speaks_result(jv):
    jv.command("maak een map")
    wait_threads()
    assert jv.voice.said[0] == "Komt in orde, meneer." and "Klaar" in jv.voice.said[-1]


def test_command_busy_message(jv):
    jv.command("lang 0.5")
    time.sleep(0.1)
    jv.command("tweede")
    wait_threads()
    assert any("nog bezig" in s for s in jv.voice.said)


def test_command_new_session(jv):
    jv.command("Start een nieuw gesprek")
    assert jv.brain.reset_called and "Schone lei" in jv.voice.said[0]


def test_command_call_me_and_long_task_calls(jv):
    class P:
        notes = []

        def notify(self, t):
            self.notes.append(t)
    jv.phone = P()
    jv.command("Bel me even")
    assert P.notes == ["U vroeg mij u te bellen."]
    jv.cfg["bel_na_minuten"] = 0.001
    jv.command("lang 0.2")
    wait_threads()
    assert len(P.notes) == 2 and "Klaar" in P.notes[1]


def test_no_twilio_means_no_phone(jarvis, home, fake_claude):
    (home / "config.json").write_text(json.dumps({"werkmap": str(home / "w"), "lokaal_luisteren": False}))
    j = jarvis.Jarvis()
    assert j.phone is None
