# Product Harvester

Paikallinen keruuohjelma. Python 3.10+; käynnistä Windowsissa `START_WINDOWS.bat`.

## Pysyvä datakansio (suositeltu)

Lähdekoodi voi sijaita GitHub Desktopin kloonatussa kansiossa, mutta SQLite ja keruutulokset pidetään sen ulkopuolella.

**Käyttöönotto:** tee ensin varmuuskopio. Jos vanha asennus on käytössä, sulje ohjelma ja kopioi (älä siirrä) vanhat `data/catalog_registry_v69.sqlite` sekä koko `workspace/` yhteen pysyvään kansioon niiden alkuperäisillä alikansiopoluilla. Älä yhdistä kahta eri SQLite-tietokantaa sokkona. Tämän jälkeen aseta Windowsin ympäristömuuttuja `PRODUCT_HARVESTER_DATA_DIR` osoittamaan tähän kansioon. Esimerkiksi PowerShellissä:

```powershell
$env:PRODUCT_HARVESTER_DATA_DIR = 'C:\\Users\\Ville\\Documents\\ProductHarvesterData'
py -3 run.py
```

Vaihtoehtoisesti:

```powershell
py -3 run.py --data-dir 'C:\\Users\\Ville\\Documents\\ProductHarvesterData'
```

Ohjelma käyttää tällöin tietokantaa `<data-dir>/data/catalog_registry_v69.sqlite` sekä tallentaa ajot `<data-dir>/workspace/`-kansioon. Jos asetusta ei anneta, vanha kansiorakenne pysyy käytössä. **Uuteen tyhjään kansioon käynnistäminen luo uuden tyhjän tietokannan; se ei siirrä vanhoja havaintoja automaattisesti.**

GitHubissa on vain ohjelmakoodi. Paikalliset tietokannat ja työtilat ovat .gitignore-poissuljettuja, mutta varmista muutokset aina ennen pushia. Älä laita yksityistä dataa julkiseen repositorioon.

Codex / Claude Code: projektiohjeet `AGENTS.md`, `CLAUDE.md`, `DEVELOPER_GUIDE.md`.
