# Beekeeper für Home Assistant

Home Assistant als Anlage in der Flottenverwaltung [beekeeper](https://beekeeper.one): Version und Zustand im Portal, Fernzugriff auf die Oberfläche ohne offene Ports, zeitlich begrenzt und im Audit-Log.

Zwei Teile:

| | |
|---|---|
| **Agent bee-ha** | hält die Verbindung zum Portal (MQTT mit Gerätezertifikat) und trägt den Fernzugriff (Tunnel). Als **Add-on** für Home Assistant OS und Supervised (Ordner `bee-ha`), als **Container** für Home Assistant in Docker. Er läuft neben Home Assistant: hängt Home Assistant, bleibt er erreichbar. |
| **Integration Beekeeper** | zeigt in Home Assistant die Verbindung zum Portal, laufende Fernzugriffe (mit Benachrichtigung und Ereignis `beekeeper_remote_access` für Automationen) und eine Taste *Fernzugriff beenden*; sie gibt dem Agenten Version, Art der Installation und Namen von Home Assistant. Ordner `custom_components/beekeeper`, über HACS. |

## Installation

**Home Assistant OS / Supervised**

1. *Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories*: die Adresse dieses Repositorys hinzufügen, dann **Beekeeper** installieren.
2. In den Optionen das Enrollment-Token aus dem Portal eintragen, starten.
3. Die Integration über HACS installieren (benutzerdefiniertes Repository, Typ *Integration*), Home Assistant neu starten. Sie wird unter *Geräte & Dienste* als gefunden angezeigt.

**Home Assistant in Docker**

1. Den Agenten als Container neben Home Assistant starten, in derselben `docker-compose.yml` (gleiches Docker-Netz). Docker baut das Image direkt aus diesem Repository:

```yaml
services:
  bee-ha:
    build: https://github.com/ioSofting/beekeeper-homeassistant.git#main:bee-ha
    image: bee-ha
    container_name: bee-ha
    hostname: bee-ha
    restart: unless-stopped
    environment:
      - BEE_TOKEN=<Enrollment-Token aus dem Portal>
      - HA_URL=http://homeassistant:8123
      - BEE_COMMENT=Haus Müller
    volumes:
      - ./bee-ha:/data
      - ./config:/config:ro
```

2. Die Integration über HACS installieren, hinzufügen und als Adresse `http://bee-ha:8099` angeben.

Einstellungen des Containers:

| Variable | Vorgabe | |
|---|---|---|
| `BEE_URL` | `https://beekeeper.one` | Adresse des Portals |
| `BEE_TOKEN` | | Enrollment-Token, nur bis zur Anmeldung; danach liegt das Gerätezertifikat in `/data` (Ordner behalten) |
| `HA_URL` | `http://homeassistant:8123` | Adresse von Home Assistant aus Sicht des Agenten; `https://` mit eigenem Zertifikat (wird nicht geprüft) |
| `HA_CONFIG` | `/config` | Konfigurationsordner von Home Assistant, nur lesend: Version aus `.HA_VERSION`; mit der Integration nicht nötig |
| `BEE_COMMENT` | | Freitext im Portal |
| `BEE_REMOTE_ACCESS` | an | `false`: kein Fernzugriff |
| `BEE_LOCAL_PORT` | `8099` | lokale API für die Integration; nicht veröffentlichen |
| `BEE_DEBUG` | aus | mehr Meldungen im Log |

Ohne Integration funktioniert der Agent auch; dann fehlen nur die Anzeige in Home Assistant und die Taste zum Beenden.

## Hinweise

- Der Agent ändert nichts an Home Assistant und braucht dort kein Konto. Beim Fernzugriff meldet man sich mit einem Konto von Home Assistant an: automatisch, wenn im Portal an der Anlage eine Anmeldung an Home Assistant hinterlegt ist, sonst im Browser. Die automatische Anmeldung wird am Ende des Fernzugriffs in Home Assistant widerrufen; Konten mit Zwei-Faktor-Anmeldung gehen dafür nicht. Am besten ein eigenes Konto anlegen, z. B. „Support“.
- Fehlanmeldungen über den Fernzugriff zählen in Home Assistant für die Adresse des Agenten. Ist die Sperre nach Fehlanmeldungen eingeschaltet (`login_attempts_threshold`), sperren zu viele davon den Fernzugriff, bis sie in `ip_bans.yaml` aufgehoben ist.
- `bee-ha/bee-ha.cjs` ist der gebündelte Agent; er wird im Repository des Servers gebaut (`npm run build:ha-addon`).

## Lizenz

Proprietär, siehe [LICENSE](LICENSE): Nutzung kostenlos erlaubt, Änderung und Weitergabe nur mit schriftlicher Erlaubnis der ioSofting GmbH.
