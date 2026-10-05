"""%INCLUDE fallback: fetch a missing include member from a DB2 "source
repository" table (legacy copybooks stored as CLOB rows) instead of
raising immediately when the member isn't a local file.

Configured via an optional "_source_repository" block in pli_dbc.json,
alongside the normal connection entries:

    { "DTO1": { "driver": "ibm_db", ... },
      "_source_repository": {
        "connection": "DTO1",
        "table": "G##OSTC.VGCC37B1",
        "name_column": "CCMOBJT",
        "type_column": "CCMTYPT",
        "source_column": "QUELLE",
        "cache_dir": ".pli_include_cache"
      } }

"connection" names an entry in the same file, reusing its driver/
Kerberos/SSL/JDBC settings. A fetched member is cached to disk under
cache_dir; once cached it is never re-fetched, and (since the cache
file is now an ordinary local file) any %INCLUDE inside it recurses
through preproc.py's normal local-file path with no new logic needed.

"table" is optional: if omitted, _resolve_ccm_table() probes
SYSIBM.SYSTABLES for whichever of _KNOWN_CCM_TABLES actually exists on
this connection (mainframe host DB2 and Linux/MFES DB2 use different
table names for the same CCM object store -- hardcoding one per
connection is what caused real, hard-to-diagnose failures before this
was added).

Runs with no Interpreter/SqlRuntime in scope (preprocessing can happen
standalone, e.g. from scripts/build.py), so this module owns its own
config search and password prompt rather than reusing SqlRuntime's.
"""
import getpass
import json
import os

from .sql import connect_raw, SQLError


class IncludeFetchError(Exception):
    pass


# The CCM object-store table lives under a different name depending on the
# DB2 environment: host DB2 (mainframe, e.g. adt0.muc.allianz) uses
# G##OSTC.VGCC37B1; Linux/MFES DB2 uses ABS.TCCMOBJ. Hardcoding one per
# connection profile is fragile (confirmed the hard way: this exact mix-up
# cost real debugging time before landing on probing the catalog instead,
# same fix ai4pli's db_extractor.py had already made for the same reason --
# credentials/config are correct, only the table differs by platform).
_KNOWN_CCM_TABLES = ["G##OSTC.VGCC37B1", "ABS.TCCMOBJ"]


def _resolve_ccm_table(conn):
    """Probe SYSIBM.SYSTABLES for whichever _KNOWN_CCM_TABLES candidate
    actually exists on this connection. Only called when "table" isn't
    set explicitly in _source_repository config."""
    cursor = conn.cursor()
    for qualified in _KNOWN_CCM_TABLES:
        creator, name = qualified.split(".", 1)
        cursor.execute(
            "SELECT 1 FROM SYSIBM.SYSTABLES WHERE CREATOR = ? AND NAME = ?",
            [creator, name])
        if cursor.fetchone():
            return qualified
    raise IncludeFetchError(
        "could not find a known CCM object-store table on this connection "
        "(tried: %s); set \"table\" explicitly in _source_repository config"
        % ", ".join(_KNOWN_CCM_TABLES))


def _default_password_prompt(prompt):
    try:
        return getpass.getpass(prompt)
    except (EOFError, KeyboardInterrupt):
        return ""


def _find_dbc_json(include_dir):
    candidates = [
        os.path.join(include_dir, "pli_dbc.json"),
        "pli_dbc.json",
        os.path.join(os.path.expanduser("~"), "pli_dbc.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def get_fallback_config(include_dir):
    """Returns (source_repo_cfg, full_dbc_config, config_dir), or
    (None, None, None) if no pli_dbc.json / no "_source_repository" key
    is found -- meaning the caller should just raise its original error."""
    path = _find_dbc_json(include_dir)
    if path is None:
        return None, None, None
    with open(path, "r", encoding="utf-8") as f:
        full_config = json.load(f)
    cfg = full_config.get("_source_repository")
    if not cfg:
        return None, None, None
    config_dir = os.path.dirname(os.path.abspath(path))
    return cfg, full_config, config_dir


def fetch_and_cache(name, cfg, full_dbc_config, config_dir,
                    password_prompt=None):
    """Fetch include member `name` from the configured source-repository
    table, caching it to disk; returns the member's source text.
    Raises IncludeFetchError on any failure (bad config, connection
    error, member not found)."""
    password_prompt = password_prompt or _default_password_prompt

    cache_dir = cfg.get("cache_dir") or ".pli_include_cache"
    if not os.path.isabs(cache_dir):
        cache_dir = os.path.join(config_dir, cache_dir)
    cache_path = os.path.join(cache_dir, name + ".pli")

    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as f:
            return f.read()

    conn_name = cfg.get("connection")
    if not conn_name:
        raise IncludeFetchError("_source_repository config is missing "
                                "\"connection\"")
    conn_key = next((k for k in full_dbc_config
                     if k.upper() == conn_name.upper()), None)
    if conn_key is None:
        raise IncludeFetchError("_source_repository connection %r not "
                                "in pli_dbc.json" % conn_name)

    name_col = cfg.get("name_column", "CCMOBJT")
    type_col = cfg.get("type_column", "CCMTYPT")
    source_col = cfg.get("source_column", "QUELLE")
    newline_char = cfg.get("newline_char", "\x9f")

    try:
        conn, driver = connect_raw(full_dbc_config[conn_key], conn_key,
                                   config_dir, password_prompt)
        table = cfg.get("table") or _resolve_ccm_table(conn)
        query = ("SELECT %s, %s, %s FROM %s WHERE %s = ?"
                 % (name_col, type_col, source_col, table, name_col))
        cur = conn.cursor()
        cur.execute(query, [name])
        row = cur.fetchone()
    except SQLError as e:
        raise IncludeFetchError("source-repository connection failed: "
                                "%s" % e)
    except IncludeFetchError:
        raise
    except Exception as e:
        raise IncludeFetchError("source-repository query failed: %s" % e)

    if row is None:
        raise IncludeFetchError("member %r not found in source "
                                "repository table %s" % (name, table))

    source = row[2]
    if hasattr(source, "getSubString"):     # JDBC CLOB proxy
        source = str(source.getSubString(1, int(source.length())))
    if source is None:
        raise IncludeFetchError("member %r has no source text in %s"
                                % (name, table))

    text = source.replace(newline_char, "\n")

    os.makedirs(cache_dir, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        f.write(text)

    return text
