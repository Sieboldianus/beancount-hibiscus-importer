"""Example importer for Hibiscus H2DB (Willhuhn)
"""

__copyright__ = "Copyright (C) 2024  Alexander Dunkel"
__license__ = "GNU GPLv2"


import csv
import datetime
import decimal
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union

import beangulp
import jaydebeapi
from beancount.core import amount, data, flags
from beangulp.testing import main
from dotenv import load_dotenv

# Configure logging
# Set logging.DEBUG or logging.INFO to increase verbosity
logging.basicConfig(
    level=logging.ERROR, format="%(asctime)s - %(levelname)s - %(message)s"
)

# supress DeprecationWarning in imported package jpype
logging.captureWarnings(False)


class Importer(beangulp.Importer):
    """An importer for H2DB Hibiscus Database."""

    def __init__(
        self,
        importer_account,
        processed_huids: Optional[str] = None,
    ):
        """Create a new importer posting to the given H2DB.

        Args:
          account: An optional account string, to filter from the H2DB
          processed_huids: An optional filepath, with hibiscus uids already processed.
        """
        # Credentials and JAR path and other parameters from .env
        load_dotenv()
        self.importer_account = importer_account
        # define hibiscus account IDs to filter
        self.hibiscus_account_ids: Dict[int, str] = get_accounts()
        self.processed_huids = get_processed_huids(processed_huids)

    def date(self, filepath):
        """Implement beangulp.Importer::date()"""
        last_mod_time = Path(filepath).stat().st_mtime
        return datetime.datetime.fromtimestamp(last_mod_time).date()

    def identify(self, filepath: Path):
        """Check for Hibiscus H2DB file name ending"""
        return str(filepath).endswith(".mv.db")

    def account(self, filepath):
        """Return the account against which we post transactions."""
        return self.importer_account

    def filename(self, filepath):
        """Return the optional renamed account filename."""
        return "hibiscus.mv.db"

    def extract(self, filepath, existing):
        """Extract a list of transactions from the H2 DB."""
        with connect_h2(filepath) as conn:
            curs = conn.cursor()
            sql_str = f"""
                SELECT *
                FROM HIBISCUS.PUBLIC.UMSATZ
                WHERE KONTO_ID in (
                {','.join([str(n) for n in self.hibiscus_account_ids])}
                )
                ORDER BY ID ASC
                LIMIT 300
                """
            # input(sql_str)
            curs.execute(sql_str)
            rows = curs.fetchall()
            # input(rows)
            # get column names
            num_fields = len(curs.description)
            if num_fields != 27:
                raise Warning(
                    "Number of columns is not 27 (expected). Check H2 database."
                )
            field_names: Dict[str, int] = {
                i[0]: ix for ix, i in enumerate(curs.description)
            }
            curs.close()
        return extract_transactions(rows, field_names, self.hibiscus_account_ids)


def extract_transactions(rows, field_names: Dict[str, int], hibiscus_account_ids):
    """Extract transactions from an Hibiscus H2DB.

    Args:
      field_names: Dictionary of fieldnames: col reference.
      hibiscus_account_ids: A list of hibiscus account ids mapped to beancount accounts
    Returns:
      A sorted list of entries.

    For column meanings, see:
    https://www.willuhn.de/wiki/doku.php?id=develop:xmlrpc:umsatz
    """
    new_entries = []
    total_items = len(rows)
    logging.info("Starting to process %d items.", total_items)
    for cnt, row in enumerate(rows):
        col_num = field_names.get("ID")
        uid = row[col_num]
        logging.debug("Processing item %d of %d, row UID: %s", cnt, total_items, uid)
        # test whether it is a balance or transaction
        amount_num = row[field_names.get("BETRAG")]
        balance = row[field_names.get("SALDO")]
        hibiscus_account_id = row[field_names.get("KONTO_ID")]
        bean_account = hibiscus_account_ids.get(hibiscus_account_id)
        if bean_account is None:
            # skip entry
            continue
        if amount_num == 0 and balance != 0:
            # create balance entry
            logging.debug("Processing balance entry")
            date_str = row[field_names.get("DATUM")]
            date = parse_hibiscus_time(date_str).date()
            balance = row[field_names.get("SALDO")]
            balance_dec = decimal.Decimal(str(balance))
            units = amount.Amount(balance_dec, "EUR")
            meta = {"lineno": uid, "filename": "hibiscus"}
            balance_entry = data.Balance(meta, date, bean_account, units, None, None)
            new_entries.append(balance_entry)
            continue
        # process transaction
        entry = build_transaction(row, field_names, hibiscus_account_ids)
        new_entries.append(entry)

    logging.info("Finished processing all items.")
    return data.sorted(new_entries)


def build_transaction(
    row: Tuple[Union[float, int, str]],
    field_names: Dict[str, int],
    hibiscus_account_ids: Dict[int, str],
) -> data.Transaction:
    """Build a single transaction.

    Args:
      row: A tuple with the transaction information.
      field_names: A dictionary to get the tuple-index for column names.
      account: An account string, the account to insert.
      currency: A currency string.
    Returns:
      A Transaction instance.
    """

    uid = row[field_names.get("ID")]  # hibiscus unique id (cross-account)
    hibiscus_account_id = row[field_names.get("KONTO_ID")]
    name = row[field_names.get("EMPFAENGER_KONTO")]  # empfaenger_name
    amount_num = row[field_names.get("BETRAG")]
    narration = row[field_names.get("ZWECK")]
    date_str = row[field_names.get("DATUM")]  # Buchungsdatum
    # payee =                 # empfaenger_name

    # Create Transaction directives.
    bean_account = hibiscus_account_ids.get(hibiscus_account_id)
    currency = "EUR"
    # if not acctid_regexp == acctid:
    #     continue

    date = parse_hibiscus_time(date_str).date()
    # Amount conversion:
    # input is java class 'java.lang.Double': Convert to python str first
    # before passing it to Decimal constructor,
    # to avoid rounding error
    amount_dec = decimal.Decimal(str(amount_num))
    units = amount.Amount(amount_dec, currency)
    posting = data.Posting(bean_account, units, None, None, None, None)
    # Build the transaction with a single leg.
    meta = data.new_metadata("<build_transaction>", 0)
    # add hibiscus unique transaction id as metadata
    meta["huid"] = str(uid)
    # There's no distinct payee.
    payee = None
    return data.Transaction(
        meta,
        date,
        flags.FLAG_OKAY,
        payee,
        narration,
        data.EMPTY_SET,
        data.EMPTY_SET,
        [posting],
    )


def parse_hibiscus_time(date_str):
    """Parse an hibiscus time string and return a datetime object.

    Args:
      date_str: A string, the date to be parsed.
    Returns:
      A datetime.datetime instance.
    """
    if len(date_str) != 10:
        raise ValueError(f"Malformed date: {date_str}")
    return datetime.datetime.strptime(date_str, "%Y-%m-%d")


def connect_h2(
    db_path, user=None, password=None, driver_jar=None
) -> jaydebeapi.Connection:
    """
    Connects to an H2 database using jaydebeapi.

    Args:
        db_path: Path to the H2 database file (e.g., "./testdb").
        user: Username for the database.
        password: Password for the database.
        driver_jar: Path to the H2 JDBC driver JAR file.
        A connection object to the database.

    See:
        https://www.h2database.com/html/features.html
    """

    user = os.getenv("H2_USER")
    password = os.getenv("H2_PASSWORD")
    driver_jar = os.getenv("H2_JAR")
    db_path = db_path.removesuffix(".mv.db")
    try:
        # JDBC URL format for encryped (CIPHER=XTEA) H2 database
        # connect in readonly-mode (ACCESS_MODE_DATA=R)
        jdbc_url = f"jdbc:h2:{db_path};CIPHER=XTEA;ACCESS_MODE_DATA=R"
        # Class name for the H2 JDBC driver
        driver_class = "org.h2.Driver"
        # Establish the connection
        conn = jaydebeapi.connect(
            driver_class,
            jdbc_url,
            [user, password],
            driver_jar,
        )

        logging.debug("Connection to H2 database successful.")
        return conn

    except Exception as e:
        logging.debug("Failed to connect to H2 database: %s", e)
        raise


def get_accounts() -> Dict[int, str]:
    """Get Accounts mapping from Hibiscus to Beancount Accounts
    from CSV

    Returns a Dictionary:
        hibiscus_account_id:beancount_account_string
    """
    file_path = Path.cwd() / os.getenv("ACCOUNTS_MAPPING_CSV")
    accounts_map = {}
    if not file_path.exists():
        raise ValueError(f"Accounts file: {file_path} not found.")
    with open(file_path, mode="r", newline="", encoding="utf-8") as csvfile:
        next(csvfile)
        reader = csv.reader(csvfile)
        for key, value in reader:
            if key.startswith("#"):
                continue
            if not key.isdigit():
                # account ID should be int
                raise ValueError(f"Malformed Hibiscus account id: {key}")
            accounts_map[int(key)] = value
    return accounts_map


def get_processed_huids(filepath: Path):
    """Get list of already processed hibiscus uids"""
    return


if __name__ == "__main__":
    importer = Importer("Assets:EUR:Hibiscus")
    main(importer)
