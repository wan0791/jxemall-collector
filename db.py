"""
数据库模块 — PostgreSQL 持久化（psycopg2 + 连接池）
"""
import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

_pool = None
_cols_cache = None


def init_db(host, dbname, user, password, port=5432):
    global _pool, _cols_cache
    _pool = pool.ThreadedConnectionPool(
        2, 10, host=host, dbname=dbname, user=user, password=password, port=port
    )
    conn = _pool.getconn()
    cur = None
    try:
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS contracts (
                id SERIAL PRIMARY KEY,
                serial_num TEXT NOT NULL,
                item_name TEXT NOT NULL DEFAULT '',
                serial_type TEXT DEFAULT '',
                title TEXT, contract_no TEXT, buyer TEXT, supplier TEXT,
                project_name TEXT, project_code TEXT, district_code TEXT,
                district_name TEXT, city_name TEXT, item_spec TEXT, item_unit TEXT,
                item_qty NUMERIC DEFAULT 0, item_unit_price NUMERIC DEFAULT 0,
                item_total_price NUMERIC DEFAULT 0, released_at TEXT,
                announcement_type TEXT, detail_url TEXT, category TEXT, brand TEXT,
                is_computer INTEGER DEFAULT 0, match_keywords TEXT DEFAULT '',
                gc_name TEXT DEFAULT '', attachments TEXT DEFAULT '',
                raw_content TEXT DEFAULT '', mark_status TEXT DEFAULT '',
                is_deleted INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(serial_num, item_name)
            )
        """)
        for tbl_sql in [
            """CREATE TABLE IF NOT EXISTS fetch_log (
                id SERIAL PRIMARY KEY, fetch_date TEXT, city_name TEXT,
                total_fetched INTEGER DEFAULT 0, new_records INTEGER DEFAULT 0,
                status TEXT DEFAULT 'ok', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, role TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS classify_log (
                id SERIAL PRIMARY KEY,
                total_records INTEGER DEFAULT 0,
                computer_count INTEGER DEFAULT 0,
                api_calls INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0,
                city_name TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
            """CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY, username TEXT NOT NULL,
                action TEXT NOT NULL, detail TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""",
        ]:
            cur.execute(tbl_sql)
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_contracts_date ON contracts(released_at)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_city ON contracts(city_name)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_computer ON contracts(is_computer)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_deleted ON contracts(is_deleted)",
            "CREATE INDEX IF NOT EXISTS idx_contracts_sn_item ON contracts(serial_num, item_name)",
        ]:
            try: cur.execute(idx_sql)
            except: pass
        _cols_cache = _get_columns(cur)
    finally:
        if cur: cur.close()
        _pool.putconn(conn)


def _get_columns(cur):
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name='contracts' ORDER BY ordinal_position
    """)
    return [r[0] for r in cur.fetchall()]


def _conn():
    return _pool.getconn()


def _put(conn):
    if conn.autocommit:
        conn.autocommit = False
    _pool.putconn(conn)


# ====== 合约记录 ======

def insert_records(records):
    """批量插入，serial_num + item_name 组合去重"""
    if not records: return 0, 0
    conn = _conn()
    new_count = 0
    skip_count = 0
    try:
        with conn.cursor() as cur:
            for r in records:
                sn = r.get("serial_num", "")
                iname = r.get("item_name", "")
                if not sn: continue
                try:
                    cur.execute("""
                        INSERT INTO contracts (serial_num, item_name, serial_type, title,
                            contract_no, buyer, supplier, project_name, project_code,
                            district_code, district_name, city_name, item_spec, item_unit,
                            item_qty, item_unit_price, item_total_price, released_at,
                            announcement_type, detail_url, category, brand, is_computer,
                            match_keywords, gc_name, attachments, raw_content, mark_status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (serial_num, item_name) DO NOTHING
                    """, (
                        sn, iname, r.get("serial_type",""), r.get("title",""),
                        r.get("contract_no",""), r.get("buyer",""), r.get("supplier",""),
                        r.get("project_name",""), r.get("project_code",""),
                        r.get("district_code",""), r.get("district_name",""),
                        r.get("city_name",""), r.get("item_spec",""), r.get("item_unit",""),
                        r.get("item_qty",0), r.get("item_unit_price",0),
                        r.get("item_total_price",0), r.get("released_at",""),
                        r.get("announcement_type",""), r.get("detail_url",""),
                        r.get("category",""), r.get("brand",""), r.get("is_computer",0),
                        r.get("match_keywords",""), r.get("gc_name",""),
                        r.get("attachments",""), r.get("raw_content",""), ""
                    ))
                    if cur.rowcount > 0: new_count += 1
                    else: skip_count += 1
                except Exception:
                    skip_count += 1
            conn.commit()
    finally:
        _put(conn)
    return new_count, skip_count


def query_contracts(date_from=None, date_to=None, city=None, computer_only=False,
                    search=None, mark=None, offset=0, limit=50):
    conn = _conn()
    try:
        conditions = ["is_deleted = 0"]
        params = []

        if date_from:
            conditions.append("released_at >= %s"); params.append(date_from)
        if date_to:
            conditions.append("released_at <= %s"); params.append(date_to)
        if city:
            conditions.append("city_name = %s"); params.append(city)
        if computer_only:
            conditions.append("is_computer = 1")
        if search:
            conditions.append("(title LIKE %s OR buyer LIKE %s OR supplier LIKE %s OR item_name LIKE %s)")
            kw = f"%{search}%"
            params.extend([kw, kw, kw, kw])
        if mark == "starred":
            conditions.append("mark_status = 'starred'")
        elif mark == "ignored":
            conditions.append("mark_status = 'ignored'")
        elif mark == "unmarked":
            conditions.append("mark_status = ''")

        where = " AND ".join(conditions)
        sql = f"SELECT * FROM contracts WHERE {where} ORDER BY released_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            cur.execute(f"SELECT COUNT(*) FROM contracts WHERE {where}", params[:-2])
            total = cur.fetchone()["count"]
            cur.execute(f"SELECT COUNT(*) FROM contracts WHERE {where} AND is_computer=1", params[:-2])
            n_computer = cur.fetchone()["count"]
        return rows, total, n_computer
    finally:
        _put(conn)


def get_distinct_dates():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT released_at FROM contracts WHERE is_deleted=0 ORDER BY released_at DESC")
            return [r[0] for r in cur.fetchall() if r[0]]
    finally: _put(conn)


def get_distinct_cities():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT city_name FROM contracts WHERE is_deleted=0 ORDER BY city_name")
            return [r[0] for r in cur.fetchall() if r[0]]
    finally: _put(conn)


def get_stats():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM contracts WHERE is_deleted=0")
            total = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT serial_num) FROM contracts WHERE is_deleted=0")
            contracts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM contracts WHERE is_deleted=0 AND mark_status='starred'")
            starred = cur.fetchone()[0]
            cur.execute("SELECT COUNT(DISTINCT serial_num) FROM contracts WHERE is_deleted=0 AND mark_status='starred'")
            starred_contracts = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM contracts WHERE is_deleted=0 AND mark_status='ignored'")
            ignored = cur.fetchone()[0]
        return {"total": total, "contracts": contracts, "starred": starred,
                "starred_contracts": starred_contracts, "ignored": ignored}
    finally: _put(conn)


# ====== 标记/编辑/批量操作 ======

def mark_record(serial_num, item_name, status):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE contracts SET mark_status=%s WHERE serial_num=%s AND item_name=%s",
                        (status, serial_num, item_name))
        conn.commit()
    finally: _put(conn)


def batch_mark(keys, status):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for sn, iname in keys:
                cur.execute("UPDATE contracts SET mark_status=%s WHERE serial_num=%s AND item_name=%s",
                            (status, sn, iname))
        conn.commit()
    finally: _put(conn)


def update_record(serial_num, item_name, field, value):
    allowed = {'buyer', 'supplier', 'item_name', 'item_spec', 'item_unit_price',
               'item_total_price', 'item_qty', 'category', 'brand'}
    if field not in allowed: return
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE contracts SET {field}=%s WHERE serial_num=%s AND item_name=%s",
                        (value, serial_num, item_name))
        conn.commit()
    finally: _put(conn)


def delete_record(serial_num, item_name):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE contracts SET is_deleted=1 WHERE serial_num=%s AND item_name=%s",
                        (serial_num, item_name))
        conn.commit()
    finally: _put(conn)


def batch_delete(keys):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for sn, iname in keys:
                cur.execute("UPDATE contracts SET is_deleted=1 WHERE serial_num=%s AND item_name=%s",
                            (sn, iname))
        conn.commit()
    finally: _put(conn)


def restore_record(serial_num, item_name):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE contracts SET is_deleted=0 WHERE serial_num=%s AND item_name=%s",
                        (serial_num, item_name))
        conn.commit()
    finally: _put(conn)


def batch_restore(keys):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            for sn, iname in keys:
                cur.execute("UPDATE contracts SET is_deleted=0 WHERE serial_num=%s AND item_name=%s",
                            (sn, iname))
        conn.commit()
    finally: _put(conn)


# ====== 日志 ======

def log_fetch(date_str, city, total, new_count):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO fetch_log (fetch_date, city_name, total_fetched, new_records, status) VALUES (%s,%s,%s,%s,%s)",
                (date_str, city, total, new_count, 'ok'))
        conn.commit()
    finally: _put(conn)


def log_action(username, action, detail=""):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO audit_log (username, action, detail) VALUES (%s,%s,%s)",
                        (username, action, detail))
        conn.commit()
    finally: _put(conn)


def log_classify(total_records, computer_count, api_calls, input_tokens, output_tokens, errors, city_name=""):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO classify_log (total_records, computer_count, api_calls,
                input_tokens, output_tokens, errors, city_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (total_records, computer_count, api_calls, input_tokens, output_tokens, errors, city_name))
        conn.commit()
    finally: _put(conn)


def get_token_stats():
    """累计 token 统计"""
    conn = _conn()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as runs, COALESCE(SUM(input_tokens),0) as total_in,
                   COALESCE(SUM(output_tokens),0) as total_out,
                   COALESCE(SUM(api_calls),0) as total_calls,
                   COALESCE(SUM(total_records),0) as total_records,
                   COALESCE(SUM(computer_count),0) as total_computer
            FROM classify_log
        """)
        r = cur.fetchone()
        last_logs = _get_last_classify_logs(cur)
        cur.close()
        price_in = 0.14 / 1_000_000
        price_out = 0.28 / 1_000_000
        rate = 7.25
        cost_in = r[1] * price_in * rate
        cost_out = r[2] * price_out * rate
        return {
            "runs": r[0], "total_in": r[1], "total_out": r[2],
            "total_tokens": r[1] + r[2], "total_calls": r[3],
            "total_records": r[4], "total_computer": r[5],
            "cost_rmb": round(cost_in + cost_out, 4),
            "last_logs": last_logs
        }
    finally: _put(conn)


def _get_last_classify_logs(cur):
    cur.execute("SELECT * FROM classify_log ORDER BY id DESC LIMIT 5")
    return [{
        "total_records": r[1], "computer_count": r[2], "api_calls": r[3],
        "input_tokens": r[4], "output_tokens": r[5], "errors": r[6],
        "city_name": r[7], "created_at": str(r[8])
    } for r in cur.fetchall()]


# ====== 用户管理 ======

def get_users():
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id, username, role, created_at FROM users ORDER BY id")
            return [dict(r) for r in cur.fetchall()]
    finally:
        _put(conn)


def create_user(username, password_hash, role="user"):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s,%s,%s)",
                (username, password_hash, role)
            )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        _put(conn)


def delete_user(user_id):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id=%s AND username!='admin'", (user_id,))
            ok = cur.rowcount > 0
        conn.commit()
        return ok
    finally:
        _put(conn)


def reset_user_password(user_id, password_hash):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                      (password_hash, user_id))
        conn.commit()
        return cur.rowcount > 0
    finally:
        _put(conn)


def get_audit_log(limit=200):
    conn = _conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT %s", (limit,))
            return [dict(r) for r in cur.fetchall()]
    finally: _put(conn)
