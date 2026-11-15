# Changelog

## 0.4.0

- Konsole: eine Shell in diesem Add-on über das Portal (Knopf *Konsole*, Recht `console`, mit Zweck und Dauer im Audit-Log), ab Werk aus (Option *Konsole erlauben*). Darin die Konfiguration von Home Assistant (`/homeassistant`), `share`, `ssl`, `media`, `addon_configs`, `backup` (nur lesen) und die CLI `ha` (amd64 und aarch64)
- Das Add-on hat dafür die Rolle `manager` beim Supervisor und die Ordner oben eingebunden

## 0.3.0

- KI-Zugang: Anfragen einer KI-Sitzung des Portals gehen an die Integration Beekeeper (ab 0.2.0), die die Werkzeuge in Home Assistant ausführt, soweit der Besitzer es in ihren Optionen erlaubt

## 0.2.0

- Erste Version als Add-on: Optionen statt Umgebungsvariablen, Version und Name von Home Assistant über den Supervisor
- Integration Beekeeper wird über Discovery gefunden; lokale API für Zustand, laufende Fernzugriffe und deren Beenden
