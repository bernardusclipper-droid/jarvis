"""
JARVIS - spraakassistent die Claude Code aanstuurt en je kan bellen.

- Lokaal: zeg "Jarvis, ..." in je microfoon (spraakherkenning op je eigen GPU).
- Telefoon: Jarvis belt je (via Twilio) als een klus klaar is, en je kunt
  tijdens het gesprek nieuwe opdrachten geven.
- Het "brein" is Claude Code (claude -p), dus Jarvis kan echt dingen doen.
"""
import asyncio
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent
# Datamap (instellingen, logboek, hulpprogramma's). De installer zet JARVIS_HOME;
# de code zelf staat in een git-map eronder zodat updates niets overschrijven.
BASE = Path(os.environ.get("JARVIS_HOME") or CODE_DIR).resolve()
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"
LOG_PATH = BASE / "jarvis.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("jarvis")

DEFAULT_CONFIG = {
    "aanspreekvorm": "meneer",
    "gpu_index": 0,
    "whisper_model": "large-v3-turbo",
    "stem": "nl-NL-MaartenNeural",
    "werkmap": "%USERPROFILE%\\JarvisProjecten",
    "veilige_modus": True,
    "lokaal_luisteren": True,
    "microfoon_drempel": 0.015,
    "bel_na_minuten": 2,
    "poort": 8765,
    "elevenlabs": {
        "api_key": "",
        "voice_id": "g5MYumTmZlFU9tYSmTp9",
        "model": "eleven_flash_v2_5",
        "gebruik": "telefoon",  # "telefoon" (spaart tegoed) of "overal"
    },
    "twilio": {
        "account_sid": "",
        "auth_token": "",
        "twilio_nummer": "",
        "jouw_nummer": "",
    },
}

GOODBYE_WORDS = ("doei", "tot ziens", "dat was het", "dat is alles", "ophangen", "hang maar op", "niks", "nee dank")


def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_PATH.exists():
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update(v)
            else:
                cfg[k] = v
    cfg["werkmap"] = os.path.expandvars(cfg["werkmap"])
    Path(cfg["werkmap"]).mkdir(parents=True, exist_ok=True)
    return cfg


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def spoken(text, limit=900):
    """Maak tekst geschikt om voor te lezen."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "een link", text)
    text = re.sub(r"[#*_>|]+", " ", text)
    text = re.sub(r"^\s*[-•]\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        text = cut[: cut.rfind(".") + 1] or cut
    return text or "Klaar."


# --------------------------------------------------------------------------
# Brein: Claude Code
# --------------------------------------------------------------------------
class Brain:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.state = load_state()
        self.claude = self._find_claude()
        naam = cfg["aanspreekvorm"]
        self.system = (
            f"Je bent JARVIS, de persoonlijke AI-assistent van de gebruiker. "
            f"Je wordt aangestuurd met spraak, via de microfoon van de pc of via de telefoon. "
            f"Antwoord altijd in het Nederlands en spreek de gebruiker aan met '{naam}'. "
            f"Je laatste antwoord wordt hardop voorgelezen: houd het kort (maximaal drie zinnen), "
            f"zonder markdown, code, opsommingstekens of volledige URL's. "
            f"Voer opdrachten zelfstandig uit in je werkmap en vat daarna in gewone spreektaal samen wat je gedaan hebt. "
            f"Vraag alleen om bevestiging bij iets onomkeerbaars of riskants. "
            f"Spraakherkenning kan fouten maken: interpreteer opdrachten verstandig."
        )

    @staticmethod
    def _find_claude():
        found = shutil.which("claude")
        if found:
            return found
        for p in (
            Path.home() / ".local" / "bin" / "claude.exe",
            Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        ):
            if p.exists():
                return str(p)
        return "claude"

    def busy(self):
        return self.lock.locked()

    def reset(self):
        self.state.pop("session_id", None)
        save_state(self.state)

    def run(self, text):
        with self.lock:
            log.info("🧠 Opdracht naar Claude Code: %s", text)
            result = self._call(text, resume=True)
            log.info("🧠 Antwoord: %s", result)
            return result

    def _call(self, text, resume):
        cmd = [self.claude, "-p", text, "--output-format", "json", "--append-system-prompt", self.system]
        if resume and self.state.get("session_id"):
            cmd += ["--resume", self.state["session_id"]]
        if self.cfg["veilige_modus"]:
            cmd += ["--permission-mode", "acceptEdits"]
        else:
            cmd += ["--dangerously-skip-permissions"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        Path(self.cfg["werkmap"]).mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(
                cmd, cwd=self.cfg["werkmap"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=3600,
                stdin=subprocess.DEVNULL, creationflags=flags,
            )
        except FileNotFoundError:
            return "Ik kan Claude Code niet vinden. Draai de installer opnieuw."
        except subprocess.TimeoutExpired:
            return "Deze opdracht duurde langer dan een uur, dus ik ben gestopt."
        try:
            data = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception:
            if resume and self.state.get("session_id"):
                self.reset()
                return self._call(text, resume=False)
            log.error("Claude fout: %s %s", p.stdout[-500:], p.stderr[-500:])
            return "Er ging iets mis met Claude Code. Bent u ingelogd? Kijk in het logbestand."
        if data.get("session_id"):
            self.state["session_id"] = data["session_id"]
            save_state(self.state)
        if data.get("is_error"):
            return "Claude Code gaf een fout: " + str(data.get("result", "onbekend"))
        return str(data.get("result", "")).strip() or "Klaar."


# --------------------------------------------------------------------------
# Stem (tekst naar spraak) via edge-tts
# --------------------------------------------------------------------------
class ElevenLabs:
    """Natuurlijke stem via ElevenLabs. Geeft None terug als het niet lukt
    (geen sleutel, tegoed op, ...), dan valt Jarvis terug op de gratis stem."""
    API = "https://api.elevenlabs.io/v1"

    def __init__(self, cfg):
        e = cfg.get("elevenlabs") or {}
        self.key = (e.get("api_key") or "").strip()
        self.voice_id = e.get("voice_id") or DEFAULT_CONFIG["elevenlabs"]["voice_id"]
        self.model = e.get("model") or "eleven_flash_v2_5"
        self.paused_until = 0.0
        self.switched = False
        self.cache = {}
        if self.key:
            log.info("🎙️ ElevenLabs-stem actief")

    @property
    def enabled(self):
        return bool(self.key) and time.time() > self.paused_until

    def _req(self, path, body=None):
        req = urllib.request.Request(
            self.API + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"xi-api-key": self.key, "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read()

    def _fallback_voice(self):
        """Gratis accounts mogen geen bibliotheekstemmen via de API gebruiken:
        kies dan automatisch een standaardstem van het account."""
        try:
            voices = json.loads(self._req("/voices")).get("voices", [])
            pre = [v for v in voices if v.get("category") == "premade"]
            male = [v for v in pre if (v.get("labels") or {}).get("gender") == "male"]
            pick = (male or pre or voices)[0]
            self.voice_id = pick["voice_id"]
            log.info("🎙️ Overgestapt op ElevenLabs-stem '%s'", pick.get("name"))
            return True
        except Exception as e:
            log.warning("Geen andere ElevenLabs-stem gevonden: %s", e)
            return False

    def tts(self, text):
        if not self.enabled:
            return None
        if text in self.cache:
            return self.cache[text]
        try:
            audio = self._req(
                f"/text-to-speech/{self.voice_id}?output_format=mp3_44100_128",
                {"text": text, "model_id": self.model, "language_code": "nl"},
            )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            low = body.lower()
            if "quota" in low or "credits" in low or e.code in (401, 429):
                self.paused_until = time.time() + 3600
                log.warning("ElevenLabs-tegoed op of geweigerd (%s). Ik gebruik een uur de gratis stem.", e.code)
            elif not self.switched and "voice" in low:
                self.switched = True
                if self._fallback_voice():
                    return self.tts(text)
            else:
                log.warning("ElevenLabs-fout %s: %s", e.code, body[:200])
            return None
        except Exception as e:
            log.warning("ElevenLabs niet bereikbaar: %s", e)
            return None
        if len(text) < 80:
            self.cache[text] = audio
        return audio


class Voice:
    def __init__(self, cfg):
        self.eleven = ElevenLabs(cfg)
        self.eleven_local = (cfg.get("elevenlabs") or {}).get("gebruik") == "overal"
        self.voice = cfg["stem"]
        self.lock = threading.Lock()
        self.speaking = threading.Event()

    def say(self, text):
        text = spoken(text)
        log.info("🔊 %s", text)
        try:
            import miniaudio
            import numpy as np
            import sounddevice as sd

            with self.lock:
                audio = (self.eleven.tts(text) if self.eleven_local else None) or asyncio.run(self._synth(text))
                dec = miniaudio.decode(audio, output_format=miniaudio.SampleFormat.SIGNED16,
                                       nchannels=1, sample_rate=24000)  # mp3 van beide stemmen
                arr = np.frombuffer(dec.samples, dtype=np.int16)
                self.speaking.set()
                try:
                    sd.play(arr, 24000)
                    sd.wait()
                    time.sleep(0.3)
                finally:
                    self.speaking.clear()
        except Exception as e:
            log.error("Spraak mislukt: %s", e)

    async def _synth(self, text):
        import edge_tts

        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, self.voice).stream():
            if chunk["type"] == "audio":
                buf += chunk["data"]
        return bytes(buf)


# --------------------------------------------------------------------------
# Oren (microfoon + Whisper op de GPU)
# --------------------------------------------------------------------------
WAKE = re.compile(r"\b(jarvis|jervis|jarvies|javis|jarviz|jarves|jaarvis)\b[\s,.!?]*", re.I)


def setup_cuda(gpu_index):
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"  # zelfde nummering als nvidia-smi
    os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu_index)
    try:
        import nvidia

        for root in list(nvidia.__path__):
            for d in Path(root).glob("*/bin"):
                os.environ["PATH"] = str(d) + os.pathsep + os.environ["PATH"]
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(str(d))
    except Exception as e:
        log.warning("CUDA-bibliotheken niet gevonden (%s)", e)


class Ears:
    SR = 16000
    BLOCK = 480  # 30 ms

    def __init__(self, cfg, voice, on_command):
        self.cfg = cfg
        self.voice = voice
        self.on_command = on_command
        self.q = queue.Queue()
        self.awaiting_until = 0.0
        self.model = self._load_model()

    def _load_model(self):
        from faster_whisper import WhisperModel

        try:
            m = WhisperModel(self.cfg["whisper_model"], device="cuda", device_index=0, compute_type="float16")
            log.info("👂 Spraakherkenning draait op GPU %s", self.cfg["gpu_index"])
            return m
        except Exception as e:
            log.warning("GPU lukt niet (%s), ik gebruik de CPU (langzamer).", e)
            return WhisperModel("small", device="cpu", compute_type="int8")

    def _callback(self, indata, frames, t, status):
        if not self.voice.speaking.is_set():
            self.q.put(indata[:, 0].copy())

    def transcribe(self, audio):
        segs, _ = self.model.transcribe(audio, language="nl", beam_size=1, initial_prompt="Jarvis,")
        return " ".join(s.text for s in segs).strip()

    def loop(self):
        import sounddevice as sd

        with sd.InputStream(samplerate=self.SR, channels=1, dtype="float32",
                            blocksize=self.BLOCK, callback=self._callback):
            log.info("👂 Ik luister. Zeg: 'Jarvis, ...'")
            while True:
                self.feed(self.q.get())

    _pre, _rec, _silent, _active = [], [], 0, False

    def feed(self, block):
        """Verwerk 30 ms microfoongeluid; knipt zinnen af op stiltes."""
        import numpy as np

        loud = float(np.sqrt(np.mean(block ** 2))) > float(self.cfg["microfoon_drempel"])
        if not self._active:
            self._pre = (self._pre + [block])[-10:]
            if loud:
                self._active, self._rec, self._silent = True, list(self._pre), 0
            return
        self._rec.append(block)
        self._silent = 0 if loud else self._silent + 1
        if self._silent > 27 or len(self._rec) > 1000:  # ~0.8 s stilte of 30 s max
            self._active = False
            if len(self._rec) - self._silent >= 10:
                self._handle(np.concatenate(self._rec))

    def _handle(self, audio):
        if self.voice.speaking.is_set():
            return
        text = self.transcribe(audio)
        if not text:
            return
        if time.time() < self.awaiting_until:
            self.awaiting_until = 0
            log.info("🎤 %s", text)
            self.on_command(text)
            return
        m = WAKE.search(text)
        if not m:
            return
        cmd = text[m.end():].strip()
        log.info("🎤 %s", text)
        if len(cmd) < 3:
            self.voice.say(f"Ja, {self.cfg['aanspreekvorm']}?")
            self.awaiting_until = time.time() + 8
        else:
            self.on_command(cmd)


# --------------------------------------------------------------------------
# Telefoon (Twilio + Cloudflare-tunnel)
# --------------------------------------------------------------------------
def norm_nr(n):
    n = re.sub(r"[^\d+]", "", n or "")
    if n.startswith("00"):
        n = "+" + n[2:]
    if n.startswith("06"):
        n = "+31" + n[1:]
    return n


class Phone:
    TTS = {"voice": "Polly.Ruben", "language": "nl-NL"}

    def __init__(self, cfg, brain, voice):
        from twilio.request_validator import RequestValidator
        from twilio.rest import Client

        t = cfg["twilio"]
        self.cfg = cfg
        self.brain = brain
        self.voice = voice
        self.client = Client(t["account_sid"], t["auth_token"])
        self.validator = RequestValidator(t["auth_token"])
        self.from_nr = norm_nr(t["twilio_nummer"])
        self.to_nr = norm_nr(t["jouw_nummer"])
        self.base = None
        self.jobs = {}
        self.msgs = {}
        self.audio = {}
        self.jlock = threading.Lock()
        self.app = self._make_app()

    # ---- opstarten ----
    def start(self):
        port = int(self.cfg["poort"])
        threading.Thread(target=lambda: self.app.run(host="127.0.0.1", port=port, threaded=True),
                         daemon=True).start()
        self._start_tunnel(port)
        try:
            nrs = self.client.incoming_phone_numbers.list(phone_number=self.from_nr)
            if nrs:
                nrs[0].update(voice_url=self.base + "/voice/in", voice_method="POST")
                log.info("📞 Bel %s om Jarvis te spreken.", self.from_nr)
        except Exception as e:
            log.warning("Kon Twilio-nummer niet koppelen voor inkomende gesprekken: %s", e)

    def _start_tunnel(self, port):
        exe = BASE / "bin" / "cloudflared.exe"
        if not exe.exists():
            exe = Path(shutil.which("cloudflared") or "cloudflared")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.tunnel = subprocess.Popen(
            [str(exe), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=flags,
        )
        deadline = time.time() + 60
        for line in self.tunnel.stdout:
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m:
                self.base = m.group(0)
                break
            if time.time() > deadline:
                break
        if not self.base:
            raise RuntimeError("Tunnel kon niet starten")
        threading.Thread(target=lambda: [None for _ in self.tunnel.stdout], daemon=True).start()
        log.info("🌐 Tunnel actief: %s", self.base)
        time.sleep(3)

    # ---- uitgaand bellen ----
    def call(self, path):
        try:
            self.client.calls.create(to=self.to_nr, from_=self.from_nr, url=self.base + path, method="POST")
            log.info("📞 Ik bel je nu op %s", self.to_nr)
        except Exception as e:
            log.error("Bellen mislukt: %s", e)

    def notify(self, text):
        mid = uuid.uuid4().hex[:12]
        self.msgs[mid] = text
        self.call(f"/voice/msg?id={mid}")

    # ---- taken tijdens een gesprek ----
    def _start_job(self, text):
        jid = uuid.uuid4().hex[:12]
        job = {"done": False, "result": None, "callback": False}
        self.jobs[jid] = job

        def work():
            res = self.brain.run(text)
            with self.jlock:
                job["done"], job["result"] = True, res
                cb = job["callback"]
            if cb:
                self.call(f"/voice/result?job={jid}")

        threading.Thread(target=work, daemon=True).start()
        return jid

    # ---- webserver voor Twilio ----
    def _make_app(self):
        from flask import Flask, Response, abort, request
        from twilio.twiml.voice_response import VoiceResponse

        app = Flask("jarvis")
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        naam = self.cfg["aanspreekvorm"]
        ph = self

        def url(p):
            return ph.base + p

        def speak(node, text):
            data = ph.voice.eleven.tts(text) if ph.voice else None
            if data:
                aid = uuid.uuid4().hex
                ph.audio[aid] = data
                while len(ph.audio) > 50:
                    ph.audio.pop(next(iter(ph.audio)))
                node.play(url(f"/audio/{aid}.mp3"))
            else:
                node.say(text, **ph.TTS)

        def twiml(r):
            return Response(str(r), mimetype="text/xml")

        def ask(r, prompt):
            g = r.gather(input="speech", language="nl-NL", speech_timeout="auto",
                         action=url("/voice/heard"), method="POST")
            speak(g, prompt)
            speak(r, f"Ik hoor niets meer. Tot uw dienst, {naam}.")
            r.hangup()
            return r

        @app.get("/audio/<aid>.mp3")
        def audio(aid):
            data = ph.audio.get(aid)
            if data is None:
                abort(404)
            return Response(data, mimetype="audio/mpeg")

        @app.before_request
        def check():
            if ph.base is None:
                abort(503)
            if request.path.startswith("/audio/"):
                return None
            full = ph.base + request.full_path.rstrip("?")
            sig = request.headers.get("X-Twilio-Signature", "")
            if not ph.validator.validate(full, request.form.to_dict(), sig):
                log.warning("Geweigerd verzoek (ongeldige handtekening)")
                abort(403)
            caller = request.form.get("From", "")
            called = request.form.get("To", "")
            if ph.to_nr not in (norm_nr(caller), norm_nr(called)):
                log.warning("Geweigerd: onbekend nummer %s", caller)
                abort(403)

        @app.post("/voice/in")
        def incoming():
            return twiml(ask(VoiceResponse(), f"Goedendag {naam}. Waarmee kan ik helpen?"))

        @app.post("/voice/heard")
        def heard():
            text = (request.form.get("SpeechResult") or "").strip()
            r = VoiceResponse()
            log.info("📞🎤 %s", text)
            if not text or any(w in text.lower() for w in GOODBYE_WORDS):
                speak(r, f"Tot uw dienst, {naam}.")
                r.hangup()
                return twiml(r)
            jid = ph._start_job(text)
            speak(r, "Komt in orde.")
            r.redirect(url(f"/voice/wait?job={jid}&n=0"), method="POST")
            return twiml(r)

        @app.post("/voice/wait")
        def wait():
            jid, n = request.args.get("job"), int(request.args.get("n", "0"))
            job = ph.jobs.get(jid)
            r = VoiceResponse()
            if not job:
                r.hangup()
                return twiml(r)
            with ph.jlock:
                done = job["done"]
                if not done and n >= 8:
                    job["callback"] = True
            if done:
                speak(r, spoken(job["result"]))
                return twiml(ask(r, "Kan ik nog iets voor u doen?"))
            if n >= 8:
                speak(r, f"Dit gaat even duren, {naam}. Ik bel u terug zodra het klaar is.")
                r.hangup()
                return twiml(r)
            r.pause(length=3)
            r.redirect(url(f"/voice/wait?job={jid}&n={n + 1}"), method="POST")
            return twiml(r)

        @app.post("/voice/result")
        def result():
            job = ph.jobs.get(request.args.get("job"), {})
            r = VoiceResponse()
            speak(r, f"Goedendag {naam}, Jarvis hier. " + spoken(job.get("result") or "De klus is klaar."))
            return twiml(ask(r, "Kan ik nog iets voor u doen?"))

        @app.post("/voice/msg")
        def msg():
            text = ph.msgs.pop(request.args.get("id"), "Ik wilde u even spreken.")
            r = VoiceResponse()
            speak(r, f"Goedendag {naam}, Jarvis hier. " + spoken(text))
            return twiml(ask(r, "Kan ik nog iets voor u doen?"))

        return app


# --------------------------------------------------------------------------
# Hoofdprogramma
# --------------------------------------------------------------------------
class Jarvis:
    def __init__(self):
        self.cfg = load_config()
        self.brain = Brain(self.cfg)
        self.voice = Voice(self.cfg)
        self.phone = None
        t = self.cfg["twilio"]
        if all(t.get(k) for k in ("account_sid", "auth_token", "twilio_nummer", "jouw_nummer")):
            try:
                self.phone = Phone(self.cfg, self.brain, self.voice)
                self.phone.start()
            except Exception as e:
                log.error("Telefoon niet beschikbaar: %s", e)
                self.phone = None
        else:
            log.info("📞 Geen Twilio ingesteld: bellen staat uit.")

    def command(self, text):
        low = text.lower()
        naam = self.cfg["aanspreekvorm"]
        if self.phone and re.search(r"\bbel (me|mij)\b", low):
            self.voice.say(f"Ik bel u, {naam}.")
            self.phone.notify("U vroeg mij u te bellen.")
            return
        if re.search(r"nieuw(e)? (gesprek|sessie)", low):
            self.brain.reset()
            self.voice.say(f"Schone lei, {naam}.")
            return
        if self.brain.busy():
            self.voice.say("Ik ben nog bezig met de vorige opdracht. Deze zet ik erachter.")
        else:
            self.voice.say(f"Komt in orde, {naam}.")
        threading.Thread(target=self._run, args=(text,), daemon=True).start()

    def _run(self, text):
        t0 = time.time()
        result = self.brain.run(text)
        self.voice.say(result)
        if self.phone and time.time() - t0 > float(self.cfg["bel_na_minuten"]) * 60:
            self.phone.notify(result)

    def start(self):
        self.voice.say(f"Jarvis is online, {self.cfg['aanspreekvorm']}.")
        if self.cfg["lokaal_luisteren"]:
            setup_cuda(self.cfg["gpu_index"])
            ears = Ears(self.cfg, self.voice, self.command)
            ears.loop()
        else:
            while True:
                time.sleep(3600)


if __name__ == "__main__":
    try:
        Jarvis().start()
    except KeyboardInterrupt:
        pass
