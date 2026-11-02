# Kaljappi V75.1 / Product Harvester 1.2.1 — Alko-peilin korjaus

## Havaittu virhe

Käyttäjän Alko-hinnastopeilin keruu pysähtyi heti ilmoitukseen `invalid literal for int() with base 10: ''`.
V75-versiossa oli vähintään kolme tämän ajon onnistumista estävää tai vaarantavaa kohtaa:

1. HTML-sivun laskurien säännöllinen lauseke hyväksyi myös pelkkiä välilyöntejä. `int('')` kaatoi ajon, kun numeron lukeminen ei onnistunut.
2. Peilisivuston `page`-parametri alkaa **nollasta**, mutta V75 muodosti ensimmäiselle sivulle `page=1` (todellisuudessa sivu 2). V75.1 pyytää ensimmäisen sivun `page=0`, toisen `page=1` jne.
3. HTTP-vastauksen tyhjä `Content-Length`-otsake aiheutti samalla tavalla `int('')`-poikkeuksen. Tyhjä otsake on nyt hyväksytty; vastauskoko rajataan silti 14 Mt:uun.

Keruun poikkeukset tallentuvat jatkossa polkuun `workspace/runs/error_run_<numero>.txt` kokonaisten Python-virhejäljitysten kanssa. Tätä tiedostoa voi käyttää Codex/Claude Code -tutkimuksessa.

## Käyttö olemassa olevalla V75-asennuksella

**Suositus: lataa pieni hotfix-paketti, ettei omat workspace-tiedostosi katoa.**

1. Sulje Product Harvesterin selain ja sitä pyörittävä komentokehote.
2. Ota varmuuskopio nykyisestä `workspace/`-kansiostasi (varsinkin sen SQLite-rekisteristä ja tallennetuista eristä).
3. Pura `kaljappi_v75_1_hotfix.zip` samaan kansioon, jossa nykyinen `START_WINDOWS.bat` sijaitsee. Hyväksy olemassa olevien `tools/harvester_desktop/*.py`-tiedostojen korvaaminen.
4. Avaa ohjelma uudelleen. Valitse **Alko → Alkon epävirallinen hinnastopeili**, aloita uusi ajo. Lokin pitäisi näyttää `Alko-peilin adapteri V75.1`.
5. Jos aiempi ajo päättyi heti virheeseen, aloita ilman Resume-valintaa. Jos aiemmassa ajossa on onnistuneita välisivuja, voit jatkaa Resume-valinnalla.

Keruun lähde on epävirallinen. Noudata sen robots.txt-sääntöjä ja käyttöehtoja. Tämä korjaus on testattu **paikallisesti simuloiduilla sivuilla**, ei loppuun asti 477-sivuisella live-keruulla.

## Testit

`python -m unittest discover -s tests -p 'test_*.py' -q` → 81/81.
`node --test tests/test_taste_search.cjs tests/test_ui_smoke.cjs` → 2/2.

Mukana on uusi integrointitesti, jossa sivu `page=0` ja sivu `page=1` tuodaan onnistuneesti SQLite-raakarekisteriin. Vanhat 351 julkaistua tuotetta ja 809 raakahavaintoa säilyvät. Julkaistuja tuotteita ei lisätä automaattisesti.
