# Kaljappi V75 / Product Harvester 1.2 — Alko-hinnasto-adapteri

## Todellinen tila 1.11.2026

**Toteutettu ja paikallisesti testattu:** Alko-muotoisen CSV/XLSX-hinnaston lukeminen, tuoteryhmien erottelu, epävirallisen verkkohinnaston sivutusparseri, 1 sivu / pyyntö, keskeytyksen tarkistuspisteet, täydellisyyden tarkistus, ajastusjonot, CSV/JSON-eräkopiot ja tuonti raakarekisteriin. V75 ei muuta Kaljapin julkaistua `products.json`-tietokantaa.

**EI vielä vahvistettu:** että koko 477-sivuisen epävirallisen hinnaston verkkokeruu onnistuu käyttäjän Windows-koneella. Verkkolähteen robots.txt/HTTP-pääsy ja tietojen uudelleenkäyttöehdot on tarkistettava. Alkon aiemmin julkaiseman XLSX-tiedoston julkinen lataus näyttää poistuneen sivustouudistuksen yhteydessä. Mikään kolmannen osapuolen hinnasto ei vahvista tämänhetkistä myymäläsaatavuutta.

### Käynnistä Windowsissa

1. Pura ZIP. **Älä kirjoita uuden ZIP:n tiedostoja vanhan V74:n päälle ilman varmuuskopiota.** Jos tuot omia V74-keruutuloksia, siirrä vanhan `workspace/`-kansion sisältö erikseen; paketti sisältää V74:n mukana säilytetyn rekisterin.
2. Asenna Python 3.10+ ja tuplaklikkaa `START_WINDOWS.bat`.
3. Selain avautuu osoitteeseen `http://127.0.0.1:8765` (tai näkyvään vaihtoehtoiseen porttiin).
4. Lähteeksi **Alko**, keruutavaksi:
   - **Alko-hinnaston CSV/XLSX**: valitse Alko-muotoinen valmiiksi hankittu CSV/XLSX-tiedosto ja paina **Aloita keruu** tai **Lisää jonoon**. Tuo vain luvallisesti hankittuja aineistoja.
   - **Alkon epävirallinen hinnastopeili**: ohjelma täyttää osoitteen `https://niisku.lab.fi/~pelja/alko/index.php`, 600 sivun turvarajan ja 2 sekunnin pyyntövälin. **Aloita keruu**. Se yrittää kerätä kaikki sivut, vain jos sivuston `robots.txt` sallii. Palvelimen rajoituksia ei ohiteta. Epävirallisen datan hyödyntämistä kaupalliseen sovellukseen ei ole hyväksytty eikä todistettu.
5. Seuraa reaaliaikaista sivu- ja tuoterivimäärää. Keskeytyneen peilikeruun jälkeen voit valita **Jatka aiemmin keskeytynyttä keräystä** ja käynnistää saman keruutavan uudelleen.
6. Täydellisen ja onnistuneen ajon tulos löytyy `workspace/runs/run_XXXXXX.csv` ja `workspace/snapshots/`-kansiosta sekä SQLite-raakarekisteristä. **Vienti Kaljappiin on REVIEW_REQUIRED -tarkistuslistan vienti, ei automaattinen julkaisu.**

### Mitä Alko-adapteri oikeasti tallentaa?

- Tuotenumero omana Alko-tunnisteenaan, tuotenimi, valmistaja, alkoholiprosentti, pakkauskoko, lähde, hinta jos saatavissa ja lähdesivu.
- Tuoteryhmä: olut, siideri, lonkero, hard seltzer. Yleiset **juomasekoitukset**, joiden tyyppi ei yksiselitteisesti selviä, merkitään `category_review`-tilaan. Viinit ja väkevät rajataan pois.
- EAN-koodi merkitään vain jos lähde *antaa oikean tarkisteeltaan kelvollisen EAN:n*. **Alkon kuusinumeroinen tuotenumero ei ole EAN.**
- Monipakkaukset tunnistetaan ja jätetään julkaisutarkistukseen, eivät erillisiksi juomiksi.
- Historiallinen tuotelista ei ole reaaliaikainen varastotieto. Puuttuvia makukuvauksia ei generoida.

### Turvallisuus ja toistettavuus

- `robots.txt` tarkistetaan, pyyntöjen väli vähintään 1,5 s ja HTTP 401/403/429 keskeyttää keruun. Ei kirjautumisen, CAPTCHA:n, rate limitin tai IP-estojen kiertoa.
- Peilissä täytyy vastata oikea sivunumero sekä ilmoittaa sivujen kokonaismäärä. Vaaditaan kaikkien sivujen onnistuminen ja hinnaston raakarivien määrän täsmääminen; osittaista keruuta ei merkitä valmiiksi.
- Kunkin sivun välitiedot tallentuvat `workspace/alko_mirror_checkpoint/`-kansioon. Tiedostotuonti on itsenäinen ja luotettavampi, jos saat täydellisen CSV/XLSX-viennin.
- Julkaistut Kaljapin 351 juomaa ja V74:n 809 aiempaa raakatuotehavaintoa säilyvät ennallaan.

### Ajastus, ohjelmointi ja testit

GUI:n peräkkäinen ajastusjono tukee `alko_file` ja `alko_mirror` -tiloja. Mukana mallityölista `data/alko_v75_runlist.csv`. Älä ajasta tiheää sivukeräystä ilman lähteen lupaa. Paikallinen Harvester on pidettävä käynnissä ajastusta varten.

CLI:

```bash
python harvester_cli.py run --source Alko --mode alko_file --file polku/hinnasto.xlsx
python harvester_cli.py run --source Alko --mode alko_mirror --url https://niisku.lab.fi/~pelja/alko/index.php --max-pages 600 --delay 2
python harvester_cli.py run --source Alko --mode alko_mirror --url https://niisku.lab.fi/~pelja/alko/index.php --max-pages 600 --delay 2 --resume
```

Aja testit:

```bash
python -m unittest discover -s tests -p test_*.py -q
node --test tests/test_taste_search.cjs tests/test_ui_smoke.cjs
```

Tärkeimmät tiedostot Codexille / Claude Codelle: `tools/harvester_desktop/alko_v75.py`, `engine.py`, `workflow_v74.py`, `server.py`, `harvester_ui/dashboard.{html,js,css}` ja `tests/test_alko_v75.py`. Samaa Product Harvester -runkoa voi edelleen käyttää kahviprojektissa.
