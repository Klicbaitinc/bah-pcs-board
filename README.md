# BAH & PCS Money Board

Free, no-login lookup of Basic Allowance for Housing by duty ZIP, military housing area, and duty station, plus a PCS move-cost checklist. Rates come from the official DoD ASCII file. Data only. No ratings. Not pay, tax, or investment advice.

Kenneth Davis runs it. It is not a Department of Defense site.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_bah.py    # downloads the DoD ZIP into data/raw/
.venv/bin/python scripts/build_site.py   # writes site/
python3 -m http.server 8765 -d site      # http://localhost:8765
```

The committed ZIP is the 2026 official bundle, so `build_site.py` works offline after `data/manifest.json` exists. `python -m unittest discover -s tests -v` parses that file and rebuilds the site.

Defaults match the GitHub Pages project site:

```bash
# export SITE_URL=https://klicbaitinc.github.io
# export BASE_PATH=/bah-pcs-board
```

Set `BASE_PATH=/` to publish at the domain root.

## Where the numbers come from

`scripts/fetch_bah.py` requests the [BAH rate-files page](https://www.travel.dod.mil/Allowances/Basic-Allowance-for-Housing/BAH-Rate-Files-Alt), then downloads the newest ASCII "all locations, all pay grades" ZIP that validates. That ZIP is the file the rate-files page distributes, from the DTMO document library:

`https://www.travel.dod.mil/Portals/119/Documents/BAH/BAH_Rates_All_Locations_All_Pay_Grades/ASCII/BAH-ASCII-{year}.zip`

The host rejects non-browser clients. The fetcher sends a normal browser header set over HTTP/2. If the download fails, the ZIP already in `data/raw/` is kept.

Tables are read from:

* `bahwYY.txt` — with dependents
* `bahwoYY.txt` — without dependents
* `sorted_zipmhaYY.txt` — ZIP to MHA
* `mhanamesYY.txt` — area names

Copies marked `old` inside the ZIP are not shown. A page notes it when the current file and the old copy differ (TX270 in the 2026 bundle). O-8, O-9, and O-10 repeat the O-7 amount. County cost groups (`ZZ…`) are real rows for ZIPs outside a named housing area. `XX499` has no rate.

`data/manifest.json` records the SHA-256, source URL, and file date.

## Pages

* `/` search
* `/zip/` search across every ZIP in the DoD file; a match opens that ZIP's housing-area page
* `/mha/{code}/` full rate table
* `/duty-station/{slug}/` the same rates framed as a duty station, not a review
* `/pcs/` move-cost checklist
* `/about/`, `/disclosure/`, `/privacy/`
* `sitemap.xml`, `robots.txt`

## Affiliates and the email list

`config/affiliates.json` holds the Penske and U-Haul placeholders (FlexOffers) and the Raisin savings placeholder. URLs are empty until a real tracking link is pasted in. Empty URLs render as "not connected" and are not outbound links. Each card has its own disclosure. The email list in `config/site.json` is a disabled placeholder. This site does not store addresses.

Nothing here is investment advice. Raisin is not a recommendation.

## Deploy

`.github/workflows/refresh-and-deploy.yml` downloads the rate file, tests, builds, commits a changed ZIP back to `main`, and deploys to GitHub Pages.

* Schedule: `20 6 * * 1` (06:20 UTC Mondays), plus every push to `main` and manual dispatch.
* The data commit only changes `data/` and includes `[skip ci]`. The workflow ignores pushes that only touch `data/`.
* Pages source must be **GitHub Actions**: Settings → Pages → Build and deployment → Source → GitHub Actions.
* Pages source is GitHub Actions. `actions/configure-pages` uploads the built `site/` directory on each run.
