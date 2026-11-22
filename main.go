// JarvisInstaller: installeert Jarvis + alles wat nodig is in één keer.
package main

import (
	"archive/zip"
	"bufio"
	"embed"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

//go:embed app/*
var appFS embed.FS

var (
	in      = bufio.NewReader(os.Stdin)
	home, _ = os.UserHomeDir()
	dir     = filepath.Join(os.Getenv("LOCALAPPDATA"), "Jarvis")
	binDir  = filepath.Join(dir, "bin")
	srcDir  = filepath.Join(dir, "src")
)

// Privé GitHub-repo met de code. Jarvis haalt hier bij elke start updates op.
const repoURL = "https://github.com/bernardusclipper-droid/jarvis.git"

func main() {
	defer func() {
		if r := recover(); r != nil {
			fmt.Println("\n❌ Er ging iets mis:", r)
		}
		ask("\nDruk op Enter om dit venster te sluiten...", "")
	}()

	fmt.Print(`
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
           Installatie (dit duurt 5-15 minuten)
`)
	must(os.MkdirAll(binDir, 0o755))

	step("1/7 Git controleren (nodig voor Claude Code en updates)")
	ensureGit()

	step("2/7 Jarvis-code ophalen in " + srcDir)
	getCode()

	step("3/7 Claude Code controleren")
	ensureClaude()

	step("4/7 Python-omgeving maken (uv)")
	uv := filepath.Join(binDir, "uv.exe")
	if !exists(uv) {
		zipPath := filepath.Join(binDir, "uv.zip")
		download("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", zipPath)
		unzipFile(zipPath, "uv.exe", uv)
		os.Remove(zipPath)
	}
	venvPy := filepath.Join(dir, ".venv", "Scripts", "python.exe")
	if !exists(venvPy) {
		run(uv, "venv", "--python", "3.12", filepath.Join(dir, ".venv"))
	}

	step("5/7 Spraakherkenning, stem en telefoon-onderdelen installeren (± 2 GB)")
	run(uv, "pip", "install", "--python", venvPy, "-r", filepath.Join(srcDir, "app", "requirements.txt"))

	step("6/7 Tunnel voor telefoongesprekken (cloudflared)")
	cf := filepath.Join(binDir, "cloudflared.exe")
	if !exists(cf) {
		download("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe", cf)
	}

	step("7/7 Instellingen")
	must(os.WriteFile(filepath.Join(dir, "Jarvis.cmd"), []byte(startScript), 0o644))
	configure()
	shortcuts()

	fmt.Println("\n✅ Jarvis is geïnstalleerd!")
	fmt.Println("   Start hem via 'JARVIS' op je bureaublad.")
	fmt.Println("   De eerste start downloadt nog het spraakmodel (± 1,5 GB).")
	if yes(ask("\nJarvis nu starten? (j/n) [j]: ", "j")) {
		exec.Command("cmd", "/c", "start", "JARVIS", filepath.Join(dir, "Jarvis.cmd")).Start()
	}
}

// ---------------------------------------------------------------- stappen

// Startscript: haalt updates op, werkt onderdelen bij en start Jarvis.
const startScript = "@echo off\r\n" +
	"title JARVIS\r\n" +
	"chcp 65001 >nul\r\n" +
	"cd /d \"%~dp0\"\r\n" +
	"set \"JARVIS_HOME=%~dp0\"\r\n" +
	"set PYTHONIOENCODING=utf-8\r\n" +
	"set \"GIT=git\"\r\n" +
	"where git >nul 2>&1 || set \"GIT=%ProgramFiles%\\Git\\cmd\\git.exe\"\r\n" +
	"if exist \"src\\.git\" (\r\n" +
	"  echo Updates zoeken...\r\n" +
	"  \"%GIT%\" -C src pull --ff-only || echo Geen update gelukt, ik start de huidige versie.\r\n" +
	")\r\n" +
	"\"bin\\uv.exe\" pip install -q --python \".venv\\Scripts\\python.exe\" -r \"src\\app\\requirements.txt\"\r\n" +
	"\".venv\\Scripts\\python.exe\" \"src\\app\\jarvis.py\"\r\n" +
	"echo.\r\n" +
	"echo Jarvis is gestopt. Zie jarvis.log voor details.\r\n" +
	"pause\r\n"

func gitPath() string {
	if p, err := exec.LookPath("git"); err == nil {
		return p
	}
	p := filepath.Join(os.Getenv("ProgramFiles"), "Git", "cmd", "git.exe")
	if exists(p) {
		return p
	}
	return ""
}

// getCode haalt de code van GitHub (zodat updates werken). Lukt dat niet,
// dan gebruiken we de kopie die in deze installer zit.
func getCode() {
	if exists(filepath.Join(srcDir, ".git")) {
		fmt.Println("   Code is er al, bijwerken...")
		if runErr(gitPath(), "-C", srcDir, "pull", "--ff-only") == nil {
			return
		}
	} else if g := gitPath(); g != "" {
		fmt.Println("   Ophalen van GitHub. Er kan een venster openen om in te loggen bij GitHub (eenmalig).")
		os.RemoveAll(srcDir)
		if runErr(g, "clone", repoURL, srcDir) == nil {
			return
		}
	}
	fmt.Println("   ⚠ GitHub lukte niet: ik gebruik de ingebouwde versie (dan geen automatische updates).")
	writeApp(srcDir)
}

func writeApp(target string) {
	must(fs.WalkDir(appFS, "app", func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		out := filepath.Join(target, filepath.FromSlash(p))
		if d.IsDir() {
			return os.MkdirAll(out, 0o755)
		}
		data, _ := appFS.ReadFile(p)
		return os.WriteFile(out, data, 0o644)
	}))
}

func ensureGit() {
	if _, err := exec.LookPath("git"); err == nil || exists(`C:\Program Files\Git\bin\bash.exe`) {
		fmt.Println("   Git is al aanwezig.")
		return
	}
	fmt.Println("   Git installeren via winget...")
	if err := runErr("winget", "install", "--id", "Git.Git", "-e", "--source", "winget",
		"--accept-package-agreements", "--accept-source-agreements", "--silent"); err != nil {
		fmt.Println("   ⚠ Automatisch installeren lukte niet. Installeer Git handmatig: https://git-scm.com/download/win")
		ask("   Druk op Enter als Git geïnstalleerd is...", "")
	}
}

func claudePath() string {
	if p, err := exec.LookPath("claude"); err == nil {
		return p
	}
	p := filepath.Join(home, ".local", "bin", "claude.exe")
	if exists(p) {
		return p
	}
	return ""
}

func ensureClaude() {
	if claudePath() == "" {
		fmt.Println("   Claude Code installeren...")
		run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "irm https://claude.ai/install.ps1 | iex")
	} else {
		fmt.Println("   Claude Code is al aanwezig.")
	}
	cp := claudePath()
	if cp == "" {
		panic("Claude Code kon niet geïnstalleerd worden. Zie https://code.claude.com")
	}
	fmt.Println("\n   Je moet één keer inloggen met je Claude Pro-account.")
	if yes(ask("   Moet je nog inloggen? Dan opent er een venster. (j/n) [j]: ", "j")) {
		exec.Command("cmd", "/c", "start", "Claude login", "/wait", cp).Run()
		ask("   Log in dat venster in, sluit het met /exit en druk hier op Enter...", "")
	}
}

func configure() {
	cfgPath := filepath.Join(dir, "config.json")
	if exists(cfgPath) && !yes(ask("   Er zijn al instellingen. Opnieuw instellen? (j/n) [n]: ", "n")) {
		return
	}
	cfg := map[string]any{}
	cfg["aanspreekvorm"] = ask("   Hoe moet Jarvis je aanspreken? [meneer]: ", "meneer")

	if out, err := exec.Command("nvidia-smi", "-L").Output(); err == nil {
		fmt.Println("\n   Gevonden videokaarten:\n   " + strings.ReplaceAll(strings.TrimSpace(string(out)), "\n", "\n   "))
	}
	gpu, _ := strconv.Atoi(ask("   Welke GPU is voor Jarvis (de NIET-game-kaart)? Nummer [1]: ", "1"))
	cfg["gpu_index"] = gpu

	fmt.Println(`
   Jarvis mag zelf bestanden maken en wijzigen in zijn werkmap.
   Veilige modus: hij mag GEEN commando's uitvoeren (bv. programma's installeren).
   Volledige modus: hij mag alles, zonder te vragen. Krachtiger, maar riskanter.`)
	cfg["veilige_modus"] = !yes(ask("   Volledige modus aanzetten? (j/n) [n]: ", "n"))

	tw := map[string]string{"account_sid": "", "auth_token": "", "twilio_nummer": "", "jouw_nummer": ""}
	fmt.Println(`
   📞 BELLEN (Twilio). Zie LEESMIJ voor hoe je een gratis Twilio-proefaccount maakt.`)
	if yes(ask("   Twilio nu instellen? (j/n) [j]: ", "j")) {
		tw["account_sid"] = ask("   Account SID (begint met AC): ", "")
		tw["auth_token"] = ask("   Auth Token: ", "")
		tw["twilio_nummer"] = ask("   Je Twilio-telefoonnummer (bv. +12025550123): ", "")
		tw["jouw_nummer"] = ask("   Jouw mobiele nummer (bv. 0612345678): ", "")
	}
	cfg["twilio"] = tw

	el := map[string]string{"api_key": "", "voice_id": "g5MYumTmZlFU9tYSmTp9", "model": "eleven_flash_v2_5", "gebruik": "telefoon"}
	fmt.Println(`
   🎙️ NATUURLIJKE STEM (ElevenLabs, optioneel)
   Sleutel maken: elevenlabs.io → Developers → API Keys → Create
   (rechten: 'Text to Speech' en 'Voices' lezen). Zonder sleutel gebruikt Jarvis de gratis stem.`)
	if key := ask("   ElevenLabs API-sleutel (Enter = overslaan): ", ""); key != "" {
		el["api_key"] = key
		fmt.Println(`
   Kies een Nederlandse stem:
     1. Alexander – warm, kalm, zelfverzekerd (aanrader voor Jarvis)
     2. Mike Kuyt – rustig, middelbare leeftijd
     3. Jan       – helder, jong, professioneel
     4. Ik heb zelf een voice_id`)
		voices := map[string]string{"1": "g5MYumTmZlFU9tYSmTp9", "2": "YamjLc9M5PjyBsXNP8uV", "3": "bmYU88QsuJdQxYmsCZdc"}
		c := ask("   Keuze [1]: ", "1")
		if v, ok := voices[c]; ok {
			el["voice_id"] = v
		} else {
			el["voice_id"] = ask("   voice_id: ", el["voice_id"])
		}
		fmt.Println(`
   Waar gebruikt Jarvis deze stem?
     1. Alleen aan de telefoon (aanrader: je gratis tegoed gaat veel langer mee)
     2. Overal, ook aan de pc`)
		if ask("   Keuze [1]: ", "1") == "2" {
			el["gebruik"] = "overal"
		}
	}
	cfg["elevenlabs"] = el

	data, _ := json.MarshalIndent(cfg, "", "  ")
	must(os.WriteFile(cfgPath, data, 0o600))
	fmt.Println("   Instellingen opgeslagen. Aanpassen kan later in:", cfgPath)
}

func shortcuts() {
	autostart := yes(ask("   Jarvis automatisch starten als de pc opstart? (j/n) [j]: ", "j"))
	target := filepath.Join(dir, "Jarvis.cmd")
	desk := filepath.Join(home, "Desktop", "JARVIS.lnk")
	startup := filepath.Join(os.Getenv("APPDATA"), `Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk`)
	mk := func(lnk string) {
		ps := fmt.Sprintf(`$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.WindowStyle=7;$s.Save()`, psq(lnk), psq(target), psq(dir))
		runErr("powershell", "-NoProfile", "-Command", ps)
	}
	mk(desk)
	if autostart {
		mk(startup)
	} else {
		os.Remove(startup)
	}
}

// ---------------------------------------------------------------- hulpjes

func step(s string) { fmt.Println("\n▶ " + s) }

func ask(q, def string) string {
	fmt.Print(q)
	s, _ := in.ReadString('\n')
	s = strings.TrimSpace(s)
	if s == "" {
		return def
	}
	return s
}

func yes(s string) bool {
	s = strings.ToLower(s)
	return strings.HasPrefix(s, "j") || strings.HasPrefix(s, "y")
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func must(err error) {
	if err != nil {
		panic(err)
	}
}

func runErr(name string, args ...string) error {
	c := exec.Command(name, args...)
	c.Stdout, c.Stderr, c.Stdin = os.Stdout, os.Stderr, os.Stdin
	return c.Run()
}

func run(name string, args ...string) {
	if err := runErr(name, args...); err != nil {
		panic(fmt.Sprintf("%s mislukt: %v", name, err))
	}
}

func download(url, dst string) {
	fmt.Println("   Downloaden:", url)
	resp, err := http.Get(url)
	must(err)
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		panic("download mislukt: " + resp.Status)
	}
	f, err := os.Create(dst)
	must(err)
	defer f.Close()
	_, err = io.Copy(f, resp.Body)
	must(err)
}

func unzipFile(zipPath, name, dst string) {
	r, err := zip.OpenReader(zipPath)
	must(err)
	defer r.Close()
	for _, f := range r.File {
		if filepath.Base(f.Name) == name {
			rc, err := f.Open()
			must(err)
			out, err := os.Create(dst)
			must(err)
			_, err = io.Copy(out, rc)
			rc.Close()
			out.Close()
			must(err)
			return
		}
	}
	panic(name + " niet gevonden in zip")
}

// psq maakt een pad veilig binnen '...' in PowerShell (bv. een naam met apostrof).
func psq(s string) string { return strings.ReplaceAll(s, "'", "''") }
