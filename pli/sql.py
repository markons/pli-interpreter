"""Embedded SQL support: the EXEC SQL ... ; statement.

Modelled on IBM's PL/I precompiler behaviour:
  EXEC SQL CONNECT TO name;            name from pli_dbc.json
  EXEC SQL SELECT ... INTO :v FROM ...;
  EXEC SQL DECLARE c CURSOR FOR ...;   OPEN / FETCH ... INTO / CLOSE
  EXEC SQL INSERT/UPDATE/DELETE/DDL ...;
  EXEC SQL COMMIT; / ROLLBACK; / CONNECT RESET;
  EXEC SQL WHENEVER SQLERROR|SQLWARNING|NOT FOUND CONTINUE|GOTO l|STOP;
  EXEC SQL INCLUDE SQLCA;              accepted, no-op

Host variables are :NAME or :STRUCT.MEMBER.  After every statement the
global variables SQLCODE (FIXED), SQLSTATE (CHAR(5)) and SQLERRM
(CHAR VARYING) are set: 0 = ok, 100 = not found, negative = error.

Connections come from pli_dbc.json, searched in: the program's
directory, the current directory, then the user's home directory:

    { "SAMPLE": { "driver": "ibm_db",
                  "url": "jdbc:db2://localhost:25000/sample",
                  "user": "db2admin" },
      "TESTDB": { "driver": "sqlite", "url": "testdb.sqlite" } }

Drivers: "sqlite" (stdlib), "ibm_db" (pip install ibm_db; the
jdbc:db2://host:port/db URL is translated to an ibm_db DSN).  A missing
"password" key triggers the interpreter's password prompt, unless
"securityMechanism": "11" is set, which selects Kerberos (no password
sent; Windows SSPI or a prior `kinit` supplies the ticket).

Kerberos + SSL together (many Db2 LUW hosts mandate both, e.g. port
50200) cannot go through ibm_db's native CLI driver -- it has no GSKit
keystore for SSL -- so that combination routes through a JDBC driver
(jaydebeapi/JPype embeds a JVM) instead. This needs: a JVM, jaydebeapi
(pip install jaydebeapi JPype1), an IBM JCC jar, and a JAAS login
config pointing at the Kerberos ticket cache (obtained via Windows SSO
or a prior `kinit`). Extra pli_dbc.json keys for that path:
"ssl" (true selects the JDBC path when combined with securityMechanism
11), "jdbc_jar_path" (falls back to DbVisualizer's bundled jcc jar --
JCC 4.32.28, needed for Kerberos -- then the IBM Data Server Driver's
db2jcc4.jar), "kerberosServerPrincipal" (the DB2 server's Kerberos
principal, e.g. "db2agl1/host@SERVER.REALM"; optional, leave unset for
auto-discovery), "realm" (the CALLER's own Kerberos realm used to
build the JAAS principal, e.g. "ALLIANZDE.ROOTDOM.NET" -- NOT the
server's realm, which belongs only in kerberosServerPrincipal; a
mismatch here causes a GSSException even with a valid ticket).
"""
import base64
import json
import os
import re
import socket
import ssl
import tempfile

from . import nodes as N


class SQLError(Exception):
    pass


_HOSTVAR_RE = re.compile(r":([A-Za-z_$#@][A-Za-z0-9_$#@]*"
                         r"(?:\.[A-Za-z_$#@][A-Za-z0-9_$#@]*)*)")
_INTO_RE = re.compile(r"\bINTO\s+(:[A-Za-z_$#@][\w$#@.]*"
                      r"(?:\s*,\s*:[A-Za-z_$#@][\w$#@.]*)*)",
                      re.IGNORECASE)


def _split_quotes(text):
    """Yield (is_quoted, fragment) pairs so host-variable scanning can
    skip SQL string literals."""
    parts = re.split(r"('(?:[^']|'')*')", text)
    for i, part in enumerate(parts):
        yield (i % 2 == 1), part


def find_host_vars(text):
    """Replace :NAME with ? placeholders; return (sql, [names])."""
    out = []
    names = []
    for quoted, frag in _split_quotes(text):
        if quoted:
            out.append(frag)
        else:
            out.append(_HOSTVAR_RE.sub(lambda m: (names.append(m.group(1)),
                                                  "?")[1], frag))
    return "".join(out), names


def _name_to_node(name, lineno=0):
    parts = name.upper().split(".")
    node = N.Ref(parts[0], None, lineno=lineno)
    for p in parts[1:]:
        node = N.Member(node, p, None, lineno=lineno)
    return node


def parse_jdbc_db2(url):
    """jdbc:db2://host:port/db -> dict(host, port, database)."""
    m = re.match(r"jdbc:db2://([^:/]+)(?::(\d+))?/(\w+)", url.strip(),
                 re.IGNORECASE)
    if not m:
        raise SQLError("cannot parse JDBC URL %r "
                       "(expected jdbc:db2://host:port/database)" % url)
    return {"host": m.group(1), "port": int(m.group(2) or 50000),
            "database": m.group(3)}


def get_server_ssl_cert_pem(host, port, timeout=10):
    """Fetch the server's leaf certificate via a throwaway TLS handshake
    and return the path to a temp PEM file (ibm_db's CLI driver takes a
    PEM path as SSLServerCertificate; it cannot read JKS truststores)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with ctx.wrap_socket(sock) as ssock:
            cert_der = ssock.getpeercert(binary_form=True)
    pem = (b"-----BEGIN CERTIFICATE-----\n" +
           base64.encodebytes(cert_der) +
           b"-----END CERTIFICATE-----\n")
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".pem",
                                      delete=False) as f:
        f.write(pem)
        return f.name


def _find_jcc_jar(cfg):
    if cfg.get("jdbc_jar_path", "").strip():
        return cfg["jdbc_jar_path"].strip()
    # JCC 4.32.28 (DbVisualizer's bundled jar) is required for Kerberos:
    # JCC 4.34.30 (IBM Data Server Driver's db2jcc4.jar) throws a DSS
    # chained-parse error against TLS 1.3-capable Db2 LUW servers even
    # when TLSv1.2 is forced client-side.
    dbviz_jar = (r"C:\Program Files\DbVisualizer\resources\dbinfo"
                 r"\templates\driverTypes\db2\maven\com\ibm\db2\jcc"
                 r"\11.5.8.0\jcc-11.5.8.0.jar")
    ibm_jar = (r"C:\Program Files\IBM\IBM DATA SERVER DRIVER"
               r"\java\db2jcc4.jar")
    if os.path.exists(dbviz_jar):
        return dbviz_jar
    if os.path.exists(ibm_jar):
        return ibm_jar
    raise SQLError("no Db2 JCC jar found for the JDBC/Kerberos+SSL path "
                   "(set \"jdbc_jar_path\" in pli_dbc.json, or install "
                   "the IBM Data Server Driver / DbVisualizer)")


def _create_jaas_config(username, realm=None):
    """Write a JAAS login config pointing DB2 JCC at the Kerberos ticket
    cache (obtained via Windows SSO or a prior `kinit`)."""
    principal = "%s@%s" % (username, realm) if realm else username
    win_user = os.environ.get("USERNAME", username).upper()
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    ticket_cache = os.path.join(user_profile,
                                "krb5cc_%s" % win_user).replace("\\", "/")
    cache_line = ('\n    ticketCache="%s"' % ticket_cache
                 if os.path.exists(ticket_cache) else "")
    module = ('    com.sun.security.auth.module.Krb5LoginModule required\n'
              '    useTicketCache=true\n'
              '    renewTGT=true\n'
              '    doNotPrompt=true\n'
              '    principal="%s"%s;' % (principal, cache_line))
    contexts = ("DB2JccConfiguration", "com.ibm.db2.jcc.DB2JccConfiguration",
               "com.sun.security.jgss.krb5.initiate", "gsslogin", "DB2")
    content = "\n\n".join("%s {\n%s\n};" % (c, module) for c in contexts)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".conf",
                                      delete=False) as f:
        f.write(content)
        return f.name


def _connect_jdbc_kerberos_ssl(cfg, host, port, database, user):
    """Kerberos + SSL via JDBC (jaydebeapi/JPype) -- the combination
    ibm_db's native CLI driver cannot do (no GSKit keystore)."""
    try:
        import jaydebeapi
    except ImportError as e:
        raise SQLError("jaydebeapi not installed (pip install jaydebeapi "
                       "JPype1) -- required for Kerberos+SSL Db2 "
                       "connections: %s" % e)

    realm = cfg.get("realm")
    jaas_path = _create_jaas_config(user, realm)
    dbviz_etc = os.path.join(os.environ.get("APPDATA", ""),
                             "DbVisualizer", "dbvis_etc")
    java_opts = [
        "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
        "-Djava.security.auth.login.config=%s" % jaas_path,
        "-Djavax.security.auth.useSubjectCredsOnly=false",
        "-Djdk.tls.client.protocols=TLSv1.2",
    ]
    krb5_conf = os.path.join(dbviz_etc, "krb5.conf")
    if os.path.exists(krb5_conf):
        java_opts.append("-Djava.security.krb5.conf=%s" % krb5_conf)
    truststore = os.path.join(dbviz_etc, "AMOSServerTruststore.jks")
    if os.path.exists(truststore):
        java_opts.append("-Djavax.net.ssl.trustStore=%s" % truststore)
        java_opts.append("-Djavax.net.ssl.trustStorePassword=changeit")
    java_tool_options = os.environ.get("JAVA_TOOL_OPTIONS", "")
    os.environ["JAVA_TOOL_OPTIONS"] = (java_tool_options + " " +
                                       " ".join(java_opts)).strip()

    jar_path = _find_jcc_jar(cfg)
    lic_dir = r"C:\Program Files\DbVisualizer\jdbc"
    jars = [jar_path] + [
        os.path.join(lic_dir, n)
        for n in ("db2jcc_license_cisuz.jar", "db2jcc_license_cu.jar")
        if os.path.exists(os.path.join(lic_dir, n))
    ]

    driver_args = {"securityMechanism": "11", "sslConnection": "true"}
    krb_principal = cfg.get("kerberosServerPrincipal", "")
    if krb_principal:
        driver_args["kerberosServerPrincipal"] = krb_principal

    jdbc_url = "jdbc:db2://%s:%d/%s" % (host, port, database)
    return jaydebeapi.connect("com.ibm.db2.jcc.DB2Driver", jdbc_url,
                              driver_args, jars)


class Cursor:
    def __init__(self, select_text):
        self.select_text = select_text
        self.dbcur = None


class SqlRuntime:
    def __init__(self, interp):
        self.interp = interp
        self.config = None
        self.connections = {}     # name -> dbapi connection
        self.current = None       # (name, connection)
        self.cursors = {}         # cursor name -> Cursor
        self.whenever = {"SQLERROR": ("CONTINUE", None),
                         "SQLWARNING": ("CONTINUE", None),
                         "NOT FOUND": ("CONTINUE", None)}

    # ---- configuration ---------------------------------------------------

    def _load_config(self):
        if self.config is not None:
            return
        candidates = [
            os.path.join(getattr(self.interp, "include_dir", "."),
                         "pli_dbc.json"),
            "pli_dbc.json",
            os.path.join(os.path.expanduser("~"), "pli_dbc.json"),
        ]
        for path in candidates:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                self.config_dir = os.path.dirname(os.path.abspath(path))
                return
        raise SQLError("no pli_dbc.json found (searched: %s)"
                       % ", ".join(candidates))

    def _connect(self, name):
        self._load_config()
        key = next((k for k in self.config if k.upper() == name.upper()),
                   None)
        if key is None:
            raise SQLError("connection %r not in pli_dbc.json" % name)
        cfg = self.config[key]
        driver = cfg.get("driver", "ibm_db").lower()
        url = cfg.get("url", "")
        if driver == "sqlite":
            import sqlite3
            path = url
            if path != ":memory:" and not os.path.isabs(path):
                path = os.path.join(self.config_dir, path)
            conn = sqlite3.connect(path)
        elif driver == "ibm_db":
            p = parse_jdbc_db2(url) if url.lower().startswith("jdbc:") \
                else {"host": cfg.get("host", "localhost"),
                      "port": cfg.get("port", 50000),
                      "database": cfg.get("database", url)}
            user = cfg.get("user", "")
            is_kerberos = str(cfg.get("securityMechanism", "")) == "11"
            if is_kerberos and cfg.get("ssl"):
                # Kerberos + SSL together needs a JVM: ibm_db's native CLI
                # driver has no GSKit keystore for SSL (many Db2 LUW hosts,
                # e.g. port 50200, mandate both at once).
                conn = _connect_jdbc_kerberos_ssl(cfg, p["host"], p["port"],
                                                  p["database"], user)
            else:
                try:
                    import ibm_db_dbi
                except ImportError as first_err:
                    # Windows: the bundled Db2 clidriver DLLs are often not
                    # on the DLL search path; register them and retry
                    added = False
                    try:
                        import site
                        dirs = list(site.getsitepackages())
                        dirs.append(site.getusersitepackages())
                    except Exception:
                        dirs = []
                    for sp in dirs:
                        bindir = os.path.join(sp, "clidriver", "bin")
                        if os.path.isdir(bindir) and hasattr(
                                os, "add_dll_directory"):
                            os.add_dll_directory(bindir)
                            os.environ["PATH"] = (bindir + os.pathsep +
                                                  os.environ.get("PATH", ""))
                            added = True
                    if not added:
                        raise SQLError("driver ibm_db not installed "
                                       "(pip install ibm_db): %s"
                                       % first_err)
                    try:
                        import ibm_db_dbi
                    except ImportError as e:
                        raise SQLError("ibm_db is installed but its Db2 "
                                       "client DLLs failed to load: %s" % e)
                dsn = ("DATABASE=%s;HOSTNAME=%s;PORT=%d;PROTOCOL=TCPIP;"
                       "UID=%s;" % (p["database"], p["host"], p["port"],
                                    user))
                if is_kerberos:
                    # Kerberos, no SSL: no password sent, Windows SSPI/
                    # kinit ticket cache handles the handshake.
                    dsn += "AUTHENTICATION=KERBEROS;"
                else:
                    dsn += "PWD=%s;" % self._password(cfg, key)
                if cfg.get("ssl"):
                    # SSL without Kerberos: ibm_db CAN do this via a
                    # fetched leaf cert (no JKS truststore support, but a
                    # PEM path works).
                    dsn += "Security=SSL;"
                    cert_path = get_server_ssl_cert_pem(p["host"],
                                                        p["port"])
                    dsn += "SSLServerCertificate=%s;" % cert_path
                conn = ibm_db_dbi.connect(dsn, "", "")
        else:
            raise SQLError("unknown driver %r (supported: sqlite, ibm_db)"
                           % driver)
        self.connections[key.upper()] = (conn, driver)
        self.current = (key.upper(), conn, driver)

    def _password(self, cfg, name):
        pwd = cfg.get("password")
        if pwd is None:
            return self.interp.password_prompt(
                "Password for connection %s: " % name)
        if isinstance(pwd, str) and pwd.startswith("env:"):
            return os.environ.get(pwd[4:], "")
        return pwd

    def _need_conn(self):
        if self.current is None:
            raise SQLError("no database connection (EXEC SQL CONNECT "
                           "TO ... first)")
        return self.current[1]

    @property
    def driver(self):
        return self.current[2] if self.current else ""

    # ---- host variables -----------------------------------------------------

    def _eval_host(self, name, env):
        from .interpreter import to_string, BitStr
        from .fixeddec import FixedDec
        v = self.interp.eval(_name_to_node(name), env)
        if isinstance(v, FixedDec):
            if self.driver == "sqlite":
                return float(v)          # sqlite3 cannot bind Decimal
            import decimal
            return decimal.Decimal(str(v))
        if isinstance(v, BitStr):
            return int(v, 2) if v else 0
        if isinstance(v, str):
            return v.rstrip()          # fixed CHAR padding vs VARCHAR
        return v

    def _assign_host(self, name, value, env):
        from .fixeddec import FixedDec
        import decimal
        if isinstance(value, decimal.Decimal):
            value = FixedDec.from_literal(str(value))
        self.interp.assign_target(_name_to_node(name), value, env)

    # ---- SQLCODE / SQLSTATE ---------------------------------------------------

    def _set_sqlca(self, code, state="00000", msg=""):
        from .interpreter import Variable, Decl
        genv = getattr(self.interp, "global_env", None)
        if genv is None:
            return
        for nm, val, decl in (
                ("SQLCODE", int(code), Decl("FIXED")),
                ("SQLSTATE", str(state)[:5].ljust(5), Decl("CHAR", 5)),
                ("SQLERRM", str(msg)[:256],
                 Decl("CHAR", 256, varying=True))):
            entry = genv.vars.get(nm)
            if entry is None:
                genv.declare(nm, Variable(val, decl))
            else:
                entry.value = val

    def _apply_whenever(self, code):
        from .interpreter import GotoSignal, StopSignal
        if code < 0:
            action = self.whenever["SQLERROR"]
        elif code == 100:
            action = self.whenever["NOT FOUND"]
        else:
            return
        kind, label = action
        if kind == "GOTO":
            raise GotoSignal(label)
        if kind == "STOP":
            raise StopSignal()

    def _fail(self, exc):
        msg = str(exc)
        m = re.search(r"SQLCODE\s*=\s*(-?\d+)", msg)
        code = int(m.group(1)) if m else -1
        m = re.search(r"SQLSTATE\s*=\s*(\w{5})", msg)
        state = m.group(1) if m else "58004"
        self._set_sqlca(code, state, msg)
        self._apply_whenever(code)

    # ---- the entry point --------------------------------------------------------

    def exec_sql(self, text, env, node):
        stmt = text.strip()
        head = re.match(r"\s*([A-Za-z]+)(?:\s+([A-Za-z_$#@][\w$#@]*))?",
                        stmt)
        w1 = (head.group(1) or "").upper() if head else ""
        w2 = (head.group(2) or "").upper() if head else ""
        try:
            if w1 == "INCLUDE":
                self._set_sqlca(0)
                return
            if w1 == "WHENEVER":
                self._whenever(stmt)
                return
            if w1 == "CONNECT":
                if w2 == "RESET":
                    self._disconnect()
                else:
                    m = re.match(r"CONNECT\s+TO\s+(\w+)", stmt,
                                 re.IGNORECASE)
                    if not m:
                        raise SQLError("bad CONNECT statement")
                    self._connect(m.group(1))
                self._set_sqlca(0)
                return
            if w1 == "SET" and w2 == "CONNECTION":
                name = stmt.split()[2].upper()
                if name not in self.connections:
                    raise SQLError("SET CONNECTION %s: not connected"
                                   % name)
                conn, drv = self.connections[name]
                self.current = (name, conn, drv)
                self._set_sqlca(0)
                return
            if w1 == "COMMIT":
                self._need_conn().commit()
                self._set_sqlca(0)
                return
            if w1 == "ROLLBACK":
                self._need_conn().rollback()
                self._set_sqlca(0)
                return
            if w1 == "DECLARE":
                m = re.match(r"DECLARE\s+(\w+)\s+CURSOR\s+"
                             r"(?:WITH\s+HOLD\s+)?FOR\s+(.*)$", stmt,
                             re.IGNORECASE | re.DOTALL)
                if not m:
                    raise SQLError("bad DECLARE CURSOR statement")
                self.cursors[m.group(1).upper()] = Cursor(m.group(2))
                self._set_sqlca(0)
                return
            if w1 == "OPEN":
                self._open_cursor(w2, env)
                return
            if w1 == "FETCH":
                self._fetch(stmt, env)
                return
            if w1 == "CLOSE":
                cur = self.cursors.get(w2)
                if cur is None:
                    raise SQLError("cursor %s not declared" % w2)
                if cur.dbcur is not None:
                    cur.dbcur.close()
                    cur.dbcur = None
                self._set_sqlca(0)
                return
            if w1 == "SELECT":
                self._select_into(stmt, env)
                return
            # everything else: INSERT / UPDATE / DELETE / DDL pass-through
            self._execute_plain(stmt, env, w1)
        except SQLError as e:
            self._set_sqlca(-1, "58004", str(e))
            self._apply_whenever(-1)
        except Exception as e:
            from .interpreter import (GotoSignal, ReturnSignal, StopSignal,
                                      LeaveSignal, IterateSignal,
                                      PLICondition, PLIError)
            if isinstance(e, (GotoSignal, ReturnSignal, StopSignal,
                              LeaveSignal, IterateSignal, PLICondition,
                              PLIError)):
                raise                       # PL/I control flow passes through
            self._fail(e)

    # ---- statement kinds -----------------------------------------------------

    def _whenever(self, stmt):
        m = re.match(r"WHENEVER\s+(SQLERROR|SQLWARNING|NOT\s+FOUND)\s+"
                     r"(CONTINUE|STOP|GO\s*TO\s+(\w+)|GOTO\s+(\w+))",
                     stmt, re.IGNORECASE)
        if not m:
            raise SQLError("bad WHENEVER statement")
        cond = re.sub(r"\s+", " ", m.group(1).upper())
        act = m.group(2).upper()
        label = (m.group(3) or m.group(4) or "").upper()
        if act.startswith(("GO", "GOTO")) and label:
            self.whenever[cond] = ("GOTO", label)
        elif act == "STOP":
            self.whenever[cond] = ("STOP", None)
        else:
            self.whenever[cond] = ("CONTINUE", None)
        self._set_sqlca(0)

    def _disconnect(self):
        if self.current is not None:
            name, conn, _ = self.current
            try:
                conn.commit()
                conn.close()
            except Exception:
                pass
            self.connections.pop(name, None)
            self.current = None
        self._set_sqlca(0)

    def _run_query(self, sql_text, env):
        conn = self._need_conn()
        sql, invars = find_host_vars(sql_text)
        params = [self._eval_host(nm, env) for nm in invars]
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur

    def _open_cursor(self, name, env):
        cur = self.cursors.get(name)
        if cur is None:
            raise SQLError("cursor %s not declared" % name)
        cur.dbcur = self._run_query(cur.select_text, env)
        self._set_sqlca(0)

    def _fetch(self, stmt, env):
        m = re.match(r"FETCH\s+(?:FROM\s+)?(\w+)\s+INTO\s+(.*)$", stmt,
                     re.IGNORECASE | re.DOTALL)
        if not m:
            raise SQLError("bad FETCH statement")
        cur = self.cursors.get(m.group(1).upper())
        if cur is None or cur.dbcur is None:
            raise SQLError("cursor %s is not open" % m.group(1))
        names = [n.strip().lstrip(":")
                 for n in m.group(2).split(",")]
        row = cur.dbcur.fetchone()
        if row is None:
            self._set_sqlca(100, "02000", "row not found")
            self._apply_whenever(100)
            return
        self._assign_row(names, row, env)
        self._set_sqlca(0)

    def _select_into(self, stmt, env):
        m = _INTO_RE.search(stmt)
        if not m:
            raise SQLError("singleton SELECT requires INTO :var,...")
        names = [n.strip().lstrip(":") for n in m.group(1).split(",")]
        sql_text = stmt[:m.start()] + stmt[m.end():]
        cur = self._run_query(sql_text, env)
        row = cur.fetchone()
        if row is None:
            self._set_sqlca(100, "02000", "row not found")
            self._apply_whenever(100)
            return
        if cur.fetchone() is not None:
            self._set_sqlca(-811, "21000",
                            "SELECT INTO returned more than one row")
            self._apply_whenever(-811)
            return
        self._assign_row(names, row, env)
        self._set_sqlca(0)

    def _assign_row(self, names, row, env):
        if len(names) != len(row):
            raise SQLError("INTO list has %d variables for %d columns"
                           % (len(names), len(row)))
        for name, value in zip(names, row):
            if value is None:
                self._set_sqlca(-305, "22002",
                                "NULL value, no indicator variable")
                self._apply_whenever(-305)
                continue
            self._assign_host(name, value, env)

    def _execute_plain(self, stmt, env, verb):
        cur = self._run_query(stmt, env)
        affected = cur.rowcount if cur.rowcount is not None else -1
        if verb in ("UPDATE", "DELETE", "INSERT") and affected == 0:
            self._set_sqlca(100, "02000", "no rows affected")
            self._apply_whenever(100)
        else:
            self._set_sqlca(0)

    def close_all(self, commit=True):
        for name, (conn, _) in list(self.connections.items()):
            try:
                if commit:
                    conn.commit()
                else:
                    conn.rollback()
                conn.close()
            except Exception:
                pass
        self.connections.clear()
        self.current = None
