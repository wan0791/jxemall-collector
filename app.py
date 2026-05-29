"""
Flask Web Dashboard — 江西电子卖场合同数据采集
"""
import os, sys, json, threading
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_file, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import functools

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import config, db, lock, collector, exporter, classifier

app = Flask(__name__, template_folder=os.path.join(ROOT, 'templates'))
app.secret_key = config.SECRET_KEY


def _init():
    db.init_db(config.DB_HOST, config.DB_NAME, config.DB_USER, config.DB_PASSWORD, config.DB_PORT)
    lock.init_lock(config.LOCK_PATH)
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM users WHERE username='admin'")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO users (username,password_hash,role) VALUES (%s,%s,%s)",
                          ('admin', generate_password_hash('admin123'), 'admin'))
            conn.commit()
    finally:
        db._put(conn)


_init()


def _log(action, detail=""):
    db.log_action(session.get('user', '?'), action, detail)


def login_required(f):
    @functools.wraps(f)
    def g(*a, **kw):
        if 'user' not in session: return redirect(url_for('login_page'))
        return f(*a, **kw)
    return g


# ====== 页面 ======

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        u, p = request.form.get('username', ''), request.form.get('password', '')
        conn = db._conn()
        r = None
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT password_hash,role FROM users WHERE username=%s", (u,))
                r = cur.fetchone()
        finally: db._put(conn)
        if r and check_password_hash(r[0], p):
            session['user'] = u; session['role'] = r[1]
            return redirect(url_for('index'))
        return render_template('login.html', error="用户名或密码错误")
    return render_template('login.html', error="")


@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('login_page'))


@app.route('/')
@login_required
def index():
    return render_template('index.html', user=session.get('user'), role=session.get('role'))


# ====== API ======

@app.route('/api/contracts')
@login_required
def api_contracts():
    dfrom = request.args.get('from', '')
    dto = request.args.get('to', '')
    city = request.args.get('city', '')
    search = request.args.get('search', '')
    mark = request.args.get('mark', 'starred')  # 默认显示电脑（已关注）
    page = int(request.args.get('page', '1'))
    page_size = int(request.args.get('page_size', '50'))
    offset = (page - 1) * page_size

    rows, total, _ = db.query_contracts(
        dfrom or None, dto or None, city or None, False, search or None,
        mark or None, offset=offset, limit=page_size
    )
    return jsonify({
        "records": rows, "count": len(rows), "total": total,
        "page": page, "page_size": page_size
    })


@app.route('/api/collect', methods=['POST'])
@login_required
def api_collect():
    if lock.is_running():
        return jsonify({"status": "blocked", "msg": "已有采集任务在运行"})
    dfrom = request.json.get('from', datetime.now().strftime("%Y-%m-%d"))
    dto = request.json.get('to', dfrom)
    cities = request.json.get('cities')

    try:
        df = datetime.strptime(dfrom, "%Y-%m-%d")
        dt = datetime.strptime(dto, "%Y-%m-%d")
        if (dt - df).days > 30:
            return jsonify({"status": "blocked", "msg": "日期范围不能超过30天"})
    except ValueError:
        pass

    def _do_collect():
        if not lock.acquire():
            return
        try:
            records = collector.collect_range(dfrom, dto, cities, max_pages_per_district=5)
            new_cnt, skip_cnt = db.insert_records(records)
            for ds in _date_range(dfrom, dto):
                db.log_fetch(ds, ",".join(cities) if cities else "全部", len(records), new_cnt)
        except Exception as e:
            print(f"Collect error: {e}")
        finally:
            lock.release()

    threading.Thread(target=_do_collect, daemon=True).start()
    _log("collect", f"{dfrom} ~ {dto}")
    return jsonify({"status": "started", "msg": f"采集任务已启动：{dfrom} ~ {dto}"})


@app.route('/api/progress')
@login_required
def api_progress():
    return jsonify(lock.get_status())


@app.route('/api/classify/start', methods=['POST'])
@login_required
def api_classify_start():
    if lock.is_running():
        return jsonify({"status": "blocked", "msg": "已有任务在运行"})
    date_from = request.json.get('from')
    date_to = request.json.get('to')
    city = request.json.get('city')
    force = request.json.get('force', False)

    def _progress_callback(done, total):
        lock.update_progress(total=total, done=done, date="AI分类", district=f"{done}/{total}")

    def _do_classify():
        if not lock.acquire():
            return
        try:
            updated, comp, stats = classifier.run_classify(
                date_from=date_from, date_to=date_to, city=city, force=force,
                progress_callback=_progress_callback)
            print(f"Classify done: {updated} updated, {comp} computer, {stats['requests']} requests, "
                  f"{stats['input_tokens']} in + {stats['output_tokens']} out tokens")
        except Exception as e:
            print(f"Classify error: {e}")
        finally:
            lock.release()

    threading.Thread(target=_do_classify, daemon=True).start()
    _log("classify", f"{date_from}~{date_to} city={city} force={force}")
    return jsonify({"status": "started", "msg": "AI 分类任务已启动"})


@app.route('/api/classify/progress')
@login_required
def api_classify_progress():
    return jsonify(lock.get_status())


@app.route('/api/tokens')
@login_required
def api_tokens():
    return jsonify(db.get_token_stats())


@app.route('/api/export')
@login_required
def api_export():
    dfrom = request.args.get('from', '')
    dto = request.args.get('to', '')
    city = request.args.get('city', '')
    mark = request.args.get('mark', '')
    path = exporter.export(dfrom or None, dto or None, city or None, mark=mark or None)
    if not path: return jsonify({"ok": False, "msg": "无数据"})
    return send_file(path, as_attachment=True)


@app.route('/api/stats')
@login_required
def api_stats():
    return jsonify(db.get_stats())


@app.route('/api/dates')
@login_required
def api_dates():
    return jsonify({"dates": db.get_distinct_dates()})


@app.route('/api/cities')
@login_required
def api_cities():
    return jsonify({"cities": db.get_distinct_cities()})


@app.route('/api/mark', methods=['POST'])
@login_required
def api_mark():
    sn = request.json.get('serial_num', '')
    iname = request.json.get('item_name', '')
    status = request.json.get('status', '')
    db.mark_record(sn, iname, status)
    _log("mark", f"{sn}/{iname} → {status}")
    return jsonify({"ok": True})


@app.route('/api/edit', methods=['POST'])
@login_required
def api_edit():
    sn = request.json.get('serial_num', '')
    iname = request.json.get('item_name', '')
    field = request.json.get('field', '')
    value = request.json.get('value', '')
    allowed = {'buyer', 'supplier', 'item_name', 'item_spec',
               'item_unit_price', 'item_total_price', 'item_qty', 'category', 'brand'}
    if field not in allowed:
        return jsonify({"ok": False, "msg": "不允许修改该字段"})
    db.update_record(sn, iname, field, value)
    _log("edit", f"{sn}/{iname} {field}={value}")
    return jsonify({"ok": True})


@app.route('/api/delete', methods=['POST'])
@login_required
def api_delete():
    sn = request.json.get('serial_num', '')
    iname = request.json.get('item_name', '')
    db.delete_record(sn, iname)
    _log("delete", f"{sn}/{iname}")
    return jsonify({"ok": True})


@app.route('/api/restore', methods=['POST'])
@login_required
def api_restore():
    sn = request.json.get('serial_num', '')
    iname = request.json.get('item_name', '')
    db.restore_record(sn, iname)
    _log("restore", f"{sn}/{iname}")
    return jsonify({"ok": True})


@app.route('/api/batch/mark', methods=['POST'])
@login_required
def api_batch_mark():
    keys = request.json.get('keys', [])
    status = request.json.get('status', '')
    if not keys: return jsonify({"ok": False, "msg": "未选中记录"})
    db.batch_mark([(k['serial_num'], k['item_name']) for k in keys], status)
    _log("batch_mark", f"{len(keys)}条 → {status}")
    return jsonify({"ok": True, "count": len(keys)})


@app.route('/api/batch/delete', methods=['POST'])
@login_required
def api_batch_delete():
    keys = request.json.get('keys', [])
    if not keys: return jsonify({"ok": False, "msg": "未选中记录"})
    db.batch_delete([(k['serial_num'], k['item_name']) for k in keys])
    _log("batch_delete", f"{len(keys)}条")
    return jsonify({"ok": True, "count": len(keys)})


@app.route('/api/batch/restore', methods=['POST'])
@login_required
def api_batch_restore():
    keys = request.json.get('keys', [])
    if not keys: return jsonify({"ok": False, "msg": "未选中记录"})
    db.batch_restore([(k['serial_num'], k['item_name']) for k in keys])
    _log("batch_restore", f"{len(keys)}条")
    return jsonify({"ok": True, "count": len(keys)})


@app.route('/api/profile', methods=['POST'])
@login_required
def api_profile():
    old_pw = request.json.get('old_password', '')
    new_pw = request.json.get('new_password', '')
    if len(new_pw) < 4:
        return jsonify({"ok": False, "msg": "新密码至少4位"})
    conn = db._conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT password_hash FROM users WHERE username=%s", (session['user'],))
            r = cur.fetchone()
            if not r or not check_password_hash(r[0], old_pw):
                return jsonify({"ok": False, "msg": "原密码错误"})
            cur.execute("UPDATE users SET password_hash=%s WHERE username=%s",
                      (generate_password_hash(new_pw), session['user']))
        conn.commit()
        return jsonify({"ok": True, "msg": "密码修改成功"})
    finally:
        db._put(conn)


def admin_required(f):
    @functools.wraps(f)
    def g(*a, **kw):
        if session.get('role') != 'admin':
            return jsonify({"ok": False, "msg": "需要管理员权限"}), 403
        return f(*a, **kw)
    return g


# ====== 用户管理 API ======

@app.route('/api/users')
@login_required
@admin_required
def api_users():
    return jsonify({"users": db.get_users()})


@app.route('/api/users', methods=['POST'])
@login_required
@admin_required
def api_create_user():
    username = request.json.get('username', '').strip()
    password = request.json.get('password', '').strip()
    role = request.json.get('role', 'user').strip()
    if not username or not password:
        return jsonify({"ok": False, "msg": "用户名和密码不能为空"})
    if len(password) < 4:
        return jsonify({"ok": False, "msg": "密码至少4位"})
    if db.create_user(username, generate_password_hash(password), role):
        _log("create_user", username)
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "用户名已存在"})


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
def api_delete_user(user_id):
    if db.delete_user(user_id):
        _log("delete_user", str(user_id))
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "删除失败（不能删除admin）"})


@app.route('/api/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
@admin_required
def api_reset_password(user_id):
    new_pw = request.json.get('password', '').strip()
    if len(new_pw) < 4:
        return jsonify({"ok": False, "msg": "密码至少4位"})
    if db.reset_user_password(user_id, generate_password_hash(new_pw)):
        _log("reset_password", str(user_id))
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "重置失败"})


def _date_range(start, end):
    d = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    while d <= e:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


if __name__ == '__main__':
    port = int(os.environ.get("PORT", "5050"))
    print(f"\n  江西电子卖场合同采集: http://127.0.0.1:{port}")
    print(f"  账号: admin / admin123\n")
    app.run(host='0.0.0.0', port=port, debug=False)
