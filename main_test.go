package main

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteAppPlacesCode(t *testing.T) {
	d := t.TempDir()
	writeApp(d)
	for _, f := range []string{"app/jarvis.py", "app/requirements.txt"} {
		if !exists(filepath.Join(d, f)) {
			t.Fatalf("%s ontbreekt", f)
		}
	}
}

func TestStartScript(t *testing.T) {
	for _, want := range []string{
		"set \"JARVIS_HOME=%~dp0\"", "-C src pull --ff-only", "src\\app\\requirements.txt",
		"\".venv\\Scripts\\python.exe\" \"src\\app\\jarvis.py\"", "%ProgramFiles%\\Git\\cmd\\git.exe",
	} {
		if !strings.Contains(startScript, want) {
			t.Errorf("startscript mist %q", want)
		}
	}
	if strings.Count(startScript, "\r\n") != strings.Count(startScript, "\n") {
		t.Error("startscript moet Windows-regeleindes hebben")
	}
	if strings.Count(startScript, "(") != strings.Count(startScript, ")") {
		t.Error("haakjes in startscript kloppen niet")
	}
}

func TestPsq(t *testing.T) {
	if psq(`C:\Users\D'Angelo`) != `C:\Users\D''Angelo` {
		t.Fatal("apostrof niet geëscaped")
	}
}

func TestRealDownloadAndUnzipUv(t *testing.T) {
	d := t.TempDir()
	z := filepath.Join(d, "uv.zip")
	download("https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip", z)
	out := filepath.Join(d, "uv.exe")
	unzipFile(z, "uv.exe", out)
	b, _ := os.ReadFile(out)
	if len(b) < 1_000_000 || string(b[:2]) != "MZ" {
		t.Fatalf("uv.exe is geen geldig Windows-programma (%d bytes)", len(b))
	}
}

func TestCloudflaredWindowsDownloadExists(t *testing.T) {
	r, err := http.Head("https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe")
	if err != nil || r.StatusCode != 200 {
		t.Fatalf("cloudflared-download niet bereikbaar: %v %v", err, r)
	}
}
