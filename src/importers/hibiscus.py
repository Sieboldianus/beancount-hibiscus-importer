"""Example importer for Hibiscus H2DB (Willhuhn)
"""

__copyright__ = "Copyright (C) 2024  Alexander Dunkel"
__license__ = "GNU GPLv2"


import csv
import subprocess
import xmlrpc.client
import datetime
import decimal
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple, Union, List

import beangulp
import jaydebeapi
from beancount.core import amount, data, flags
from beangulp.testing import main
from dotenv import load_dotenv

# Configure logging
# Set logging.DEBUG or logging.INFO to increase verbosity
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)


class Importer(beangulp.Importer):
    """An importer for H2DB Hibiscus Database."""

    def __init__(
        self, source: Optional[str] = None, ignore_already_processed: bool = None
    ):
        """Create a new importer posting to the given H2DB.

        Args:
          source: specify whether to query from H2 database or via XML-RPC
          ignore_already_processed: will ignore all hibiscus unique ids already imported
            based on PROCESSED_HUIDS_FILE
        """
        # Credentials and JAR path and other parameters from .env
        load_dotenv()
        # this value is set as the header on export; it is usually not relevant,
        # as individual transactions are assigned to beancount accounts via .accounts
        self.importer_account = "Assets:EUR:Hibiscus"
        # define hibiscus account IDs to filter
        accounts_map, payee_map = get_accounts()
        self.hibiscus_account_ids: Dict[int, str] = accounts_map
        self.hibiscus_payees: Dict[int, str] = payee_map
        self.processed_huids = set()
        # get from env, will override what is given to init()
        if ignore_already_processed is not None:
            self.ignore_already_processed = ignore_already_processed
        else:
            self.ignore_already_processed = os.getenv("IGNORE_ALREADY_PROCESSED")
        if self.ignore_already_processed:
            self.processed_huids = get_processed_huids()
        self.source: str = "H2"
        # self.source: str = "RPC"
        if source is not None:
            self.source = source

    def date(self, filepath):
        """Implement beangulp.Importer::date()"""
        # try to get last mod date via git first
        git_last_mod_time = get_last_git_mod(filepath)
        if git_last_mod_time is None:
            file_last_mod_time = Path(filepath).stat().st_mtime
            return datetime.datetime.fromtimestamp(file_last_mod_time).date()
        return git_last_mod_time

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
        # get optional env vars
        limit_count = os.getenv("LIMIT_ENTRIES")
        if limit_count is not None:
            limit_count = int(limit_count)
        limit_since = os.getenv("SINCE_DATE")
        limit_huid = os.getenv("SINCE_HUID")
        if limit_huid is not None:
            limit_huid = int(limit_huid)
        if self.source == "RPC":
            transactions_raw = get_from_rpc(
                hibiscus_account_ids=self.hibiscus_account_ids
            )
        elif self.source == "H2":
            transactions_raw = get_from_h2(
                filepath, self.hibiscus_account_ids, limit_count, limit_since,
                limit_huid)
        else:
            raise ValueError(f"Source {self.source} not supported.")
        return extract_transactions(
            transactions_raw,
            self.hibiscus_account_ids,
            self.processed_huids,
            self.ignore_already_processed,
            self.hibiscus_payees
        )


def get_from_rpc(
    server_url: Optional[str] = None,
    hibiscus_account_ids: Optional[Dict[int, str]] = None,
):
    """Get Hibiscus transactions via XML-RPC. This method supports importing
    automatic Hibiscus categories."""
    rows = connect_rpc()
    return rows


def get_from_h2(
    filepath,
    hibiscus_account_ids,
    limit_count: Optional[int] = None,
    limit_since: Optional[str] = None,
    limit_huid: Optional[int] = None,
) -> List[Dict[str, Union[str, int, float]]]:
    """Get Hibiscus transactions from H2 db.

    Args:
        filepath: Path to H2 DB
        hibiscus_account_ids: Account IDs to filter
        limit_count: Number of items to return from the H2 DB (optional)
        limit_since: Limit by date (optional)
        limit_huid: Limit by HUID (optional)
    """
    limit_sql= ""
    if limit_count:
        limit_sql = f"LIMIT {limit_count}"
    limit_since_sql = ""
    if limit_since:
        limit_since_sql = f"AND DATUM > '{limit_since}'"
    limit_huid_sql = ""
    if limit_huid:
        limit_huid_sql = f"AND ID > '{limit_huid}'"
    with connect_h2(filepath) as conn:
        curs = conn.cursor()
        sql_str = f"""
            SELECT *
            FROM HIBISCUS.PUBLIC.UMSATZ
            WHERE KONTO_ID in (
            {','.join([str(n) for n in hibiscus_account_ids])}
            )
            {limit_since_sql}
            {limit_huid_sql}
            ORDER BY ID ASC
            {limit_sql}
            """

        curs.execute(sql_str)
        rows = curs.fetchall()

        # get column names
        num_fields = len(curs.description)
        if num_fields != 27:
            raise Warning("Number of columns is not 27 (expected). Check H2 database.")
        field_names: Dict[str, int] = {
            i[0]: ix for ix, i in enumerate(curs.description)
        }
        field_names = [ix[0] for ix in curs.description]
        curs.close()
        transactions_raw = build_dict(rows, field_names)
    return transactions_raw


def build_dict(rows, field_names):
    """Build a list of dictionaries from a list of column names and rows (tuples).
    Keys will be converted to lowercase, to match the XML-RTC returned style
    """
    field_names = [key.lower() for key in field_names]
    return [dict(zip(field_names, values)) for values in rows]


def extract_transactions(
    rows, hibiscus_account_ids, already_processed_huids, ignore_already_processed,
    hibiscus_payees: Dict[str, str],
):
    """Extract transactions from an Hibiscus H2DB.

    Args:
      rows: DB Rows to process
      hibiscus_account_ids: A list of hibiscus account ids mapped to beancount accounts
      already_processed_huids: Set of HUIDs already processed
      hibiscus_payees: Optional mapping of payees (empfaenger) to beancount accounts
    Returns:
      A sorted list of entries.

    For column meanings, see:
    https://www.willuhn.de/wiki/doku.php?id=develop:xmlrpc:umsatz
    """
    new_entries = []
    skipped = 0
    total_items = len(rows)
    newly_processed_huids = set()
    logging.info("Starting to process %d items.", total_items)
    for cnt, row in enumerate(rows):
        huid = row.get("id")
        if (ignore_already_processed == True) and str(huid) in already_processed_huids:
            skipped += 1
            continue
        logging.debug("Processing item %d of %d, row HUID: %s", cnt, total_items, huid)
        # test whether it is a balance or transaction
        amount_num = row.get("betrag")
        balance = row.get("saldo")
        hibiscus_account_id = int(row.get("konto_id"))
        bean_account = hibiscus_account_ids.get(hibiscus_account_id)

        amount_num = fix_regional(amount_num)
        balance = fix_regional(balance)

        if bean_account is None:
            # skip accounts not in mapping
            continue
        # convert over float to int, to prevent
        # ValueError: invalid literal for int() with base 10
        if int(float(amount_num)) == 0 and float(balance) > 0:
            # create balance entry
            balance_entry = build_balance(row, bean_account)
            new_entries.append(balance_entry)
            newly_processed_huids.add(huid)
            continue
        # process transaction
        entry = build_transaction(row, hibiscus_account_ids, hibiscus_payees)
        new_entries.append(entry)
        # add huid to set of newly processed huids, to be written to cache later
        newly_processed_huids.add(huid)
    logging.info("Finished processing all items.")

    if ignore_already_processed == True:
        logging.info(
            "Skipped %d already processed items, based on the Hibiscus uid.", skipped
        )
        write_processed_huids(newly_processed_huids)
    else:
        logging.info(
            "Already processed items are not skipped this time.")
    # reconcile and merge internal transactions
    reconciled_entries = merge_transactions(new_entries)
    return data.sorted(new_entries)

def merge_transactions(
        entries: List[Union[data.Transaction, data.Balance]]) -> List[Union[data.Transaction, data.Balance]]:
    """Walk through list of transactions and merge those
    that come from two (internal) accounts
    """
    reconciled_entries = entries
    return reconciled_entries

def build_balance(
    row: Tuple[Union[float, int, str]],
    bean_account: str,
) -> data.Balance:
    """Build a single balance"""
    logging.debug("Processing balance entry")
    huid = row.get("id")
    date_str = row.get("datum")
    date = parse_hibiscus_time(date_str).date()
    balance = row.get("saldo")
    balance_dec = decimal.Decimal(str(balance))
    units = amount.Amount(balance_dec, "EUR")
    # Build the transaction with a single leg.
    meta = data.new_metadata("<build_transaction>", 0)
    # add hibiscus unique transaction id as metadata
    meta = {"lineno": huid, "filename": "hibiscus"}
    balance_entry = data.Balance(meta, date, bean_account, units, None, None)
    return balance_entry


def build_transaction(
    row: Tuple[Union[float, int, str]],
    hibiscus_account_ids: Dict[int, str],
    hibiscus_payees: Dict[str, str],
) -> data.Transaction:
    """Build a single transaction.

    Args:
      row: A tuple with the transaction information.
      hibiscus_account_ids: A dictionary to get the brancount account.
      currency: A currency string.
    Returns:
      A Transaction instance.
    """

    uid = row.get("id")  # hibiscus unique id (cross-account)
    hibiscus_account_id = int(row.get("konto_id"))
    payee_account_ref = row.get("empfaenger_konto")  # empfaenger_name
    amount_num = row.get("betrag")
    narration = row.get("zweck")
    date_str = row.get("datum")  # Buchungsdatum
    # payee_name =                 # empfaenger_name

    # Create Transaction directives.
    bean_account = hibiscus_account_ids.get(hibiscus_account_id)
    currency = "EUR"

    date = parse_hibiscus_time(date_str).date()
    # for xml-rpc, all numbers use decimal commas (Germany),
    # but in Python we prefer decimal comma

    amount_num = fix_regional(amount_num)
    # Amount conversion:
    # for H2 db query, the input is java class 'java.lang.Double':
    # Convert to python str first
    # before passing it to Decimal constructor,
    # to avoid rounding error
    amount_dec = decimal.Decimal(str(amount_num))
    units = amount.Amount(amount_dec, currency)
    posting = data.Posting(bean_account, units, None, None, None, None)
    # Build the transaction with a single leg.
    meta = data.new_metadata("<build_transaction>", 0)
    # add hibiscus unique transaction id as metadata
    meta["huid"] = str(uid)
    # if int(uid) == 2952:
    #    input(row)
    postings = [posting]
    payee_posting = None
    # check if this is an internal transaction
    # between own hibiscus accounts
    payee_account = hibiscus_payees.get(payee_account_ref)
    if payee_account is not None:
        # build second leg of posting
        payee_posting = data.Posting(
            payee_account, -units, None, None, None, None)
        postings.append(payee_posting)
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
        postings,
    )


def fix_regional(str_num: Union[str, any]) -> float:
    """Ugly method to fix regionalization for numbers in XML.
    This replaced ',' with '.'
    str_num can also be of class java.lang.Double (obj),
    in case of H2 DB query
    """
    if isinstance(str_num, str) and not str_num.isdigit() and "," in str_num:
        return str_num.replace(",", ".")
    return str_num


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


def clean_filters(filters):
    """
    Remove keys with None values from the filter dictionary.
    """
    return {k: v for k, v in filters.items() if v is not None}


def connect_rpc():
    """Connect to Hibiscus via XML-RPC interface"""
    # Define the server URL
    server_url = "http://127.0.0.1:8080/xmlrpc"
    try:
        # Create a server proxy object
        server = xmlrpc.client.ServerProxy(
            server_url,
            allow_none=True,
            verbose=False,  # set to True for debugging XML-RPC
        )

        raw_filters = {
            "konto_id": None,  # Filter by account id
            "datum:min": "2024-01-20",  # Start date (YYYY-MM-DD)
            "datum:max": "2024-12-31",  # End date (YYYY-MM-DD)
            "verwendungszweck": None,  # Filter by purpose text
            "valuta:min": None,  # Minimum amount
            "valuta:max": None,  # Maximum amount
        }

        # clean filters to remove None values
        filter_criteria = clean_filters(raw_filters)

        transactions = server.hibiscus.xmlrpc.umsatz.list(filter_criteria)

    except xmlrpc.client.Fault as fault:
        print(f"XML-RPC Fault occurred: {fault}")
    except xmlrpc.client.ProtocolError as err:
        print(f"A protocol error occurred: {err.url} - {err.errcode} {err.errmsg}")
    return transactions


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


def get_accounts() -> Tuple[Dict[int, str], Dict[str, str]]:
    """Get Accounts mapping from Hibiscus to Beancount Accounts
    from CSV

    Returns two dictionaries:
        hibiscus_account_id:beancount_account_string
        hibiscus_payee_ref:beancount_account_string
    """
    file_path = Path.cwd() / os.getenv("ACCOUNTS_MAPPING_CSV")
    accounts_map = {}
    payee_map = {}
    if not file_path.exists():
        raise ValueError(f"Accounts file: {file_path} not found.")
    with open(file_path, mode="r", newline="", encoding="utf-8") as csvfile:
        next(csvfile)
        reader = csv.reader(csvfile)
        for key, value, payee in reader:
            if key.startswith("#"):
                continue
            if not key.isdigit():
                # account ID should be int
                raise ValueError(f"Malformed Hibiscus account id: {key}")
            accounts_map[int(key)] = value
            if payee:
                payee_map[payee] = value
    return accounts_map, payee_map


def get_huids_file():
    """Get filepath for huids file via env"""
    processed_huids_file = Path.cwd() / os.getenv("PROCESSED_HUIDS_FILE")
    if not processed_huids_file.parents[0].is_dir():
        raise ValueError(f"HUIDs file: {processed_huids_file} folder not found.")
    if not processed_huids_file.exists():
        # create file if it does not exist
        processed_huids_file.touch()
    return processed_huids_file


def get_processed_huids():
    """Get list of already processed hibiscus uids"""
    processed_huids = set()
    file_path = get_huids_file()
    with open(file_path, mode="r", newline="", encoding="utf-8") as f:
        lines = f.read().splitlines()
        processed_huids.update(lines)
    return processed_huids


def write_processed_huids(newly_processed_huids):
    """Append a list of newly processed hibiscus uids"""
    file_path = get_huids_file()
    if not file_path.exists():
        raise ValueError(f"HUIDs file: {file_path} not found.")
    with open(file_path, mode="a", newline="", encoding="utf-8") as f:
        for huid in newly_processed_huids:
            f.write(f"{huid}\n")


def get_last_git_mod(filepath: Path) -> datetime.datetime.date:
   """Get last git mode time for filepath"""
   # try to get last mod date via git first
   try:
       git_last_mod_time = subprocess.run(
           ['git', 'log', '-1', '--pretty="format:%ci"', filepath],
           check=True, text=True, stdout=subprocess.PIPE)
   except:
       return
   git_last_mod_time = git_last_mod_time.stdout.split('\n')[0]
   date_format = "%Y-%m-%d %H:%M:%S %z"
   # cleanup
   git_last_mod_time = git_last_mod_time.removeprefix('"format:')
   git_last_mod_time = git_last_mod_time.removesuffix('"')
   # Convert to datetime object
   return datetime.datetime.strptime(git_last_mod_time, date_format).date()

if __name__ == "__main__":
    # hook for tests
    importer = Importer(source="H2", ignore_already_processed=False)
    main(importer)
