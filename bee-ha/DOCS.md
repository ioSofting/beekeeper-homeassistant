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

## Hinweise

- Fehlanmeldungen über den Fernzugriff zählen in Home Assistant für die Adresse des Add-ons. Ist die Sperre nach Fehlanmeldungen eingeschaltet (`login_attempts_threshold`), sperren zu viele davon den Fernzugriff, bis sie in `ip_bans.yaml` aufgehoben ist.
- Wenn Home Assistant hängt oder neu startet, bleibt das Add-on verbunden: das Portal zeigt dann „antwortet nicht“.
