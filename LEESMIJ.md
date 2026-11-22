# JARVIS – installatie

## Wat je nodig hebt
- Windows 10/11 pc met internet
- Je Claude Pro-account
- (Voor bellen) een gratis Twilio-proefaccount – zie hieronder

## Stap 1 – Twilio-account maken (± 10 min, vooraf)
1. Ga naar **twilio.com/try-twilio** en maak een gratis account (je krijgt startkrediet, ongeveer $15).
2. Bevestig **je eigen mobiele nummer** (dat moet: proefaccounts mogen alleen naar bevestigde nummers bellen).
3. Klik in de Console op **"Get a phone number"** → je krijgt gratis een Amerikaans nummer.
4. Op de startpagina van de Console staan je **Account SID** en **Auth Token**. Houd die bij de hand.
5. Ga naar **Voice → Settings → Geo permissions** en zet **Netherlands** aan.

## Stap 1b – ElevenLabs-stem (optioneel, natuurlijke stem)
1. Log in op **elevenlabs.io** → **Developers → API Keys → Create API Key**.
2. Geef de sleutel de rechten **Text to Speech** en **Voices** (lezen).
3. Plak de sleutel in de installer en kies een stem.

De ElevenLabs-stem wordt gebruikt aan de pc én aan de telefoon.
- **Gratis account** (10.000 tekens per maand, ongeveer 10–15 minuten spraak): de Nederlandse stemmen uit de bibliotheek zijn waarschijnlijk niet beschikbaar.
  Jarvis kiest dan automatisch een standaardstem van ElevenLabs die Nederlands spreekt. Die klinkt natuurlijk, maar met een licht accent.
- **Starter-abonnement (± $5 per maand)**: de echte Nederlandse stemmen (Alexander, Mike, Jan) werken.
- **Tegoed op?** Dan schakelt Jarvis vanzelf over op de gratis stem en probeert het een uur later opnieuw.

## Stap 2 – Installeren
1. Dubbelklik **JarvisInstaller.exe**.
   Windows zegt waarschijnlijk "Windows heeft uw pc beschermd" (de .exe is niet ondertekend) → klik **Meer info → Toch uitvoeren**.
2. Volg de vragen. De installer regelt Git, Claude Code, Python, spraakherkenning en de tunnel.
3. Log één keer in bij Claude Code als het venster opent, typ daarna `/exit`.
4. Kies welke GPU voor Jarvis is (de installer laat ze zien).

## Gebruik
- **Aan de pc:** zeg *"Jarvis, maak een website over mijn hond"*, of zeg eerst *"Jarvis"* en wacht op *"Ja, meneer?"*.
- **"Jarvis, bel me"** → je telefoon gaat over.
- **Lange klussen** (langer dan 2 minuten) → Jarvis belt je als het klaar is.
- **Aan de telefoon:** zeg wat er moet gebeuren. Is het snel klaar, dan hoor je het antwoord meteen; anders hangt Jarvis op en belt hij terug.
- **Jarvis zelf bellen:** bel je Twilio-nummer (dit is een internationaal gesprek naar de VS en kost je beltegoed).
- **"Jarvis, nieuw gesprek"** → hij vergeet het vorige gesprek.

Projecten komen in `C:\Users\<jij>\JarvisProjecten`.

## Instellingen
`%LOCALAPPDATA%\Jarvis\config.json` (openen via Win+R en dan dat pad plakken):
| Instelling | Betekenis |
|---|---|
| `gpu_index` | welke videokaart (nummer zoals in `nvidia-smi -L`) |
| `veilige_modus` | `true` = alleen bestanden maken/wijzigen; `false` = mag ook programma's draaien en installeren |
| `microfoon_drempel` | hoger zetten (bv. `0.03`) als hij op achtergrondgeluid reageert |
| `bel_na_minuten` | na hoeveel minuten werk hij je belt |
| `stem` | gratis reservestem, bv. `nl-BE-ArnaudNeural` |
| `elevenlabs.voice_id` | andere ElevenLabs-stem |
| `elevenlabs.api_key` | leegmaken = ElevenLabs uit |

Problemen? Kijk in `%LOCALAPPDATA%\Jarvis\jarvis.log`.

## Kosten
- ElevenLabs: gratis tot 10.000 tekens per maand, daarna de gratis stem.
- Spraakherkenning, reservestem en tunnel: **gratis** (draait op je eigen pc).
- Claude Code: je Pro-abonnement. Let op: zware klussen gebruiken je limiet.
- Twilio-proefaccount: **gratis** zolang het startkrediet duurt (genoeg voor tientallen gesprekken). Een proefaccount laat eerst een kort Engels zinnetje horen en vraagt je op een toets te drukken; daarna praat Jarvis.
  Daarna kost het ongeveer $1,15 per maand voor het nummer plus een paar cent per minuut.

## Opnieuw installeren of bijwerken
Draai de installer gewoon opnieuw: je instellingen blijven bewaard.
