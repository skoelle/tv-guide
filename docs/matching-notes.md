# Matching-Notizen

Bekannte Fehlertypen und ihre Behandlung. Grundlage: SPEC.md 5.5, Prototyp `tools/match_test.py` v2.1.

## Bekannte Fehlzuordnungen

- **Senderformate vor dem Strich**: `"Schlager Gold - Bianca"` wird als Werk missverstanden. Der Präfix-Treffer ist hier eine Sendung, kein Titel. Lösung: Review bzw. Override, ggf. Sendergruppe ignorieren.
- **Gleicher Name, andere Produktion**: Mehrere Werke heißen gleich. Typ und Laufzeit bewerten; bei Abstand unter 0,05 zum Zweitplatzierten greift der Ambiguitäts-Abzug (−0,15) und die Show landet im Review.
- **Reihen ohne eigenen Serieneintrag**: z. B. `"Polizeiruf 110: Abgründe"`. Es gibt keinen IMDb-Eintrag für die Reihe, der volle Titel trifft den Film `"Abgründe"`. Ergebnis `review`; erwartete Korrektur über Override.
- **Dokureihen, die IMDb als Serie führt**: Über Review und Override lösen.

## Vorfilter (Skip)

- `skip_title`: Titel aus der `[skip].titles`-Liste (Teleshopping, Sendeschluss, …).
- `skip_short`: Median-Laufzeit unter `skip_min_duration` Minuten.
- `skip_block`: ohne Kategorie, ab `block_min_duration` Minuten und `block_min_events` Terminen (typisch Radio-/Musikstrecken).
- `skip_category`: EPG-Kategorie beginnt mit einem Eintrag aus `[skip].categories`.
- Zusätzlich werden ganze Sender über `channels_ignore.txt` ausgeschlossen (Radios, Shopping).

## Vertrag

- `normalize()` ist identisch in `tvguide/normalize.py`, `tools/imdb_build.py` und `tools/match_test.py`. Änderungen erfordern einen Neuaufbau von `imdb.db` (Version in `meta.normalize_version`, noch offen).
- Die Golden-Referenz `tests/golden/matches.csv` stammt aus einem echten `match_test.py`-Lauf. Weicht eine Zuordnung ab, schlägt der Golden-Test fehl.
