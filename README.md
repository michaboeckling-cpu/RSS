# RSS-Feed für öffentliche Ausschreibungen: Social Media

Dieses kleine Projekt erzeugt aus dem Open-Data-Export des Datenservice Öffentlicher Einkauf eine `rss.xml`.

## Was gefiltert wird

Standardmäßig:

- `Social Media`
- `Social-Media`
- nur Releases mit den OCDS-Tags `tender` oder `tenderUpdate`, soweit Tags vorhanden sind
- die letzten 30 Kalendertage
- maximal 100 RSS-Einträge

Die Einstellungen stehen in `config.json`.

## Einrichtung auf GitHub

1. Neues Repository anlegen, zum Beispiel `vergabe-rss`.
2. Den kompletten Inhalt dieses Ordners in das Repository hochladen.
3. Unter `Actions` den Workflow `RSS aktualisieren` einmal manuell starten.
4. Prüfen, ob danach im Repository eine Datei `rss.xml` vorhanden ist.
5. Unter `Settings` > `Pages` bei `Build and deployment` als Source `Deploy from a branch` auswählen.
6. Branch `main` und Ordner `/ (root)` auswählen und speichern.
7. Nach erfolgreicher Veröffentlichung lautet die Feed-Adresse typischerweise:

   `https://DEIN-GITHUB-NAME.github.io/vergabe-rss/rss.xml`

8. Diese URL in Outlook als RSS-Feed eintragen.

## Aktualisierung

Der Workflow läuft viermal täglich. Zusätzlich kann er unter `Actions` jederzeit manuell gestartet werden.

## Suchbegriffe ändern

In `config.json` zum Beispiel:

```json
"keywords": [
  "Social Media",
  "Social-Media",
  "Employer Branding",
  "Kreativagentur"
]
```

`require_all_keywords: false` bedeutet ODER-Verknüpfung.

## Hinweis zur Datenquelle

Das Projekt greift nicht auf die DTVP-Suchergebnisse zu und scrapt DTVP nicht. Es nutzt den Open-Data-Export des Datenservice Öffentlicher Einkauf. Daher kann die Ergebnismenge von einer DTVP-Suche abweichen.

## Lokaler Test

Mit Python 3:

```bash
python generate_feed.py
```

Danach sollte `rss.xml` im selben Ordner liegen.
