# Beancount Hibiscus Importer

[Hibiscus](https://github.com/willuhn/hibiscus) is a widely used open source software in Germany. It supports the retrieval of transactions from banks using the HBCI or FinTS standards and (with plugins) via web scraping (credit cards) or APIs (e.g. Paypal). This **Beancount Hibiscus Importer** provides import functionality to retrieve transactions from the Hibiscus H2DB (either directly or via XML-RPC) and convert them to the Beancount format using the v3 [Beangulp](https://github.com/beancount/beangulp) interface.

-----

## TL;DR (Setup)

On Linux or Windows Subsystem for Linux (WSL1 or 2), follow these steps to install and run this importer.

First make sure Java is available.
```bash
apt update && apt install default-jre
java -version
> openjdk version "11.0.25" 2024-10-15
```

Clone this repository.
```bash
git clone git@github.com:Sieboldianus/beancount-hibiscus-importer.git
cd beancount-hibiscus-importer
```

Create a virtual environment in a local `.venv` folder and install dependencies.
```bash
python3 -m venv .venv
source ./.venv/bin/activate
pip install beangulp beancount python-dotenv jaydebeapi
```

Set up custom environment variables. Either use a `.env` file or export 
variables manually. See [.env.example](.env.example) for a description of configuration
settings.
```bash
cp .env.example .env
nano .env # edit your settings
```

Run the tests:
```bash
bash src/importers/runtests.sh
```

Run the importer. Beangulp will hook all `.mv.db` (Hibiscus H2DB) 
files to the Hibiscus importer:
```bash
python import.py identify ./path/to/h2db
python import.py extract ./path/to/h2db > tmp.beancount
```

## Features

- **Balances** and **Transactions** are currently imported from Hibiscus
- The Hibiscus H2 DB is opened in read-only mode.
- **Hibiscus Unique IDs**: The Hibiscus H2DB uses unique IDs to deduplicate transactions. These UIDs are used by the importer to avoid duplicate imports. They are called `huid` (**h**ibiscus **u**nique **id**s). The `huid`s are added as metadata to beancount transactions to keep a reference to the Hibiscus database:
  ```
  2023-12-11 * "LAG INTERNET SERVICES GMB FIBU 52344 RENR 3021233243280"
    huid: "22"
    Assets:EUR:DKB:Gemeinschaftskonto  -23.94 EUR
  ```
- **Duplicate detection**: `huid`s are also used to avoid importing hibiscus transactions multiple times. A cache of already processed `huid`s is kept in `PROCESSED_HUIDS_FILE`. This feature can be turned off in the importer function (set `ignore_already_processed=False`)
- **Account Mappings**: Multiple Hibiscus Accounts are mapped to Beancount accounts through [hibiscus/.accounts](hibiscus/.accounts) (set via `ACCOUNTS_MAPPING_CSV`).
- Choose either `H2`, to query the Hibiscus database directly, or `RPC`, to query via the Hibiscus XML-RPC interface (see below)
- A number features are implemented to restrict querying H2 transactions, e.g. limit by number of entries (`LIMIT_ENTRIES`), by last date (`SINCE_DATE`), or by HUID (`SINCE_HUID`).

## Configuration

I decided to move most configuration to environment variables. Feel free to set these any way you want. Have
a look at [.env.example](.env.example) for a description of configuration settings.

The _common_ way to set env settings in Linux is to use a file called `.env`. This file is automatically loaded.
Beancount Hibiscus Importer imitates this behaviour. Note that you should usually _not_ commit `.env` to git.

## Notes

- Only one connection per H2DB is allowed. Close Hibiscus before importing to beancount from the H2DB
- **Not implemented**: If you use Hibiscus to categorize transactions, these categories are not yet used to categorize the second leg of beancount transactions (e.g. expense accounts).
- **Language conventions**: The code is primarily written in English. All references to Hibiscus (e.g. H2 column names) are kept in German.

## XML-RPC

Note: XML-RPC query is currently a proof of concept. Not all H2 DB query features are implemented.

Querying automated transaction categories from Hibiscus is only possible via XML-RPC protocol, 
as automated categories are not stored in H2 itself. See 
[the documentation](https://www.willuhn.de/wiki/doku.php?id=develop:xmlrpc). You can still 
query Hibiscus categories from the H2DB after transactions have been manually marked as `verified` in Hibiscus 
(make sure to confirm hard-linking categories in this case). 

I still wanted to be able to query via XML-RPC, as this also allows connecting to Hibiscus 
running on a remote server. Note that the XML-RPC mapping of values is currently not included 
in the tests and the processing of values is a bit rough (`str`, `int`, and `float` conversion; regionalization).

<details><summary>Setup of Hibiscus XML-RPC connection</summary>

Datei > Einstellungen > Verfügbare Plugins > `<Alle Repositories>` auswählen
- `hibiscus.xmlrpc` finden und installieren, installiert dependencies:
    - `jameica.webadmin`
    - `jameica.xmlrpc`

Datei > Einstellungen > HTTP
- Server binden an: `127.0.0.1`
- no HTTPS
- no auth

</details>