# Beancount Hibiscus Importer

[Hibiscus](https://github.com/willuhn/hibiscus) is a widely used open source software in Germany. It supports the retrieval of transactions from banks using the HBCI or FinTS standards and (with plugins) via web scraping (credit cards) or APIs (e.g. Paypal). This **Beancount Hibiscus Importer** provides import functionality to retrieve transactions from the Hibiscus H2DB and convert them to the Beancount format using the v3 [Beangulp](https://github.com/beancount/beangulp) interface.

-----

## TL;DR (Setup)

On Linux or Windows Subsystem for Linux (WSL1 or 2), follow these steps to install and run this importer.

First make sure Java is available.
```bash
sudo apt update
sudo apt install default-jre
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
variables manually.
```bash
cp .env.example .env
nano .env # edit your settings
```

Run the tests:
```bash
bash src/importers/runtests.sh
```

Run the importer:
```bash
python import.py identify ./downloads
python import.py extract ./downloads > tmp.beancount
```

## Features

- **Hibiscus Unique IDs**: The Hibiscus H2DB uses unique IDs to deduplicate transactions. These UIDs are used by the importer to avoid duplicate imports. They are called `huid` (**h**ibiscus **u**nique **id**s). The `huid`s are added as metadata to beancount transactions to keep a reference to the Hibiscus database:
```
2023-12-11 * "LAG INTERNET SERVICES GMB FIBU 52344 RENR 3021233243280"
  huid: "22"
  Assets:EUR:DKB:Gemeinschaftskonto  -23.94 EUR
```
- **Account Mappings**: Multiple Hibiscus Accounts are mapped to Beancount accounts through [hibiscus/.accounts](hibiscus/.accounts).
- **Balances** and **Transactions** are currently imported from Hibiscus
- The Hibiscus H2 DB is opened in read-only mode.


**Not implemented**: If you use Hibiscus to categorize transactions, these categories are not yet used to categorize the second leg of beancount transactions (e.g. expense accounts).

**Language conventions**: The code is primarily written in English. All references to Hibiscus (e.g. H2 column names) are kept in German.