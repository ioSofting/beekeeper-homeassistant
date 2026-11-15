# Beekeeper

Bindet dieses Home Assistant als Anlage in die Flottenverwaltung [beekeeper](https://beekeeper.one) ein:

- Heartbeat und Inventar: Version von Home Assistant, Home Assistant OS oder Supervised, Name des Zuhauses
- Fernzugriff auf die Oberfläche von Home Assistant über einen Tunnel, ohne offene Ports und ohne Portfreigabe im Router; zeitlich begrenzt, mit Person und Zweck im Audit-Log des Portals

Das Add-on ändert nichts an Home Assistant und braucht dort kein Konto. Beim Fernzugriff meldet man sich mit einem Konto von Home Assistant an: automatisch, wenn im Portal an der Anlage eine Anmeldung an Home Assistant hinterlegt ist, sonst im Browser. Am besten dafür ein eigenes Konto anlegen, z. B. „Support“.

## Einrichten

1. Im Portal die Anlage anlegen und unter *Gerät* ein **Enrollment-Token** erzeugen.
2. Das Token in der Konfiguration dieses Add-ons eintragen und das Add-on starten.
3. Home Assistant meldet die Integration **Beekeeper** unter *Einstellungen → Geräte & Dienste* als gefunden. Hinzufügen: Dann zeigt Home Assistant die Verbindung zum Portal, laufende Fernzugriffe (mit Benachrichtigung) und eine Taste *Fernzugriff beenden*. Die Integration gibt es über HACS (dieses Repository als benutzerdefiniertes Repository, Typ Integration).

Nach der Anmeldung liegt das Gerätezertifikat in den Daten des Add-ons. Das Token wird danach nicht mehr gebraucht. Wer das Add-on deinstalliert, verliert das Zertifikat; danach meldet es sich mit einem neuen Token als neues Gerät an.

## Optionen

| Option | |
|---|---|
| Enrollment-Token | aus dem Portal, nur bis zur ersten Anmeldung |
| Server | Adresse des Portals, Vorgabe `https://beekeeper.one` |
| Kommentar | Freitext im Portal, z. B. Kunde und Ort |
| Fernzugriff erlauben | aus: das Portal kann keinen Fernzugriff öffnen und zeigt das an |
| Adresse von Home Assistant | nur wenn Home Assistant nicht unter `http://homeassistant:8123` antwortet, etwa `https://homeassistant:8123` mit eigenem Zertifikat (wird nicht geprüft) |
| Debug | mehr Meldungen im Log |

## Konsole

Mit der Option *Konsole erlauben* (ab Werk aus) öffnet das Portal eine Shell in diesem Add-on, als Terminal im Browser: für Personen mit dem Recht `console`, mit Zweck und Dauer, im Audit-Log, und hier als Benachrichtigung zu sehen, solange sie läuft. Kein SSH-Add-on und kein offener Port nötig.

In der Shell (als `root` im Container des Add-ons):

- `/homeassistant`: die Konfiguration von Home Assistant (dort startet die Shell), dazu `/share`, `/ssl`, `/media`, `/addon_configs` und `/backup` (nur lesen)
- `ha`: die CLI von Home Assistant, z. B. `ha core logs`, `ha core check`, `ha core update`, `ha addons`, `ha backups new`, `ha host logs` (nicht auf armv7)
- `bash`, `nano`, `less`, `curl`, `jq`

Dafür hat das Add-on beim Supervisor die Rolle `manager` und die Ordner eingebunden; Home Assistant zeigt deshalb eine niedrigere Sicherheitsbewertung. Der Host selbst ist nicht erreichbar.

## KI-Zugang

Mit der Integration Beekeeper ab 0.2.0 kann eine KI im Portal mit Werkzeugen in Home Assistant arbeiten, solange jemand dort einen KI-Zugang geöffnet hat. Was sie darf (lesen, ändern, Benutzer verwalten), legt der Besitzer in den Optionen der Integration fest; ab Werk ist alles aus. Das Add-on reicht die Anfragen nur weiter.

## Hinweise

- Fehlanmeldungen über den Fernzugriff zählen in Home Assistant für die Adresse des Add-ons. Ist die Sperre nach Fehlanmeldungen eingeschaltet (`login_attempts_threshold`), sperren zu viele davon den Fernzugriff, bis sie in `ip_bans.yaml` aufgehoben ist.
- Wenn Home Assistant hängt oder neu startet, bleibt das Add-on verbunden: das Portal zeigt dann „antwortet nicht“.
