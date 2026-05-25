"""
任务锁 — 防止手动任务重复触发，支持进度追踪
"""
import os, json, time, errno


_LOCK_FILE = None


def init_lock(lock_path):
    global _LOCK_FILE
    _LOCK_FILE = lock_path


def _stale():
    """锁文件是否过期（>30分钟），过期则删除"""
    if not _LOCK_FILE or not os.path.exists(_LOCK_FILE):
        return False
    if time.time() - os.path.getmtime(_LOCK_FILE) > 1800:
        try: os.unlink(_LOCK_FILE)
        except: pass
        return True
    return False


def is_running():
    if not _LOCK_FILE: return False
    _stale()
    return os.path.exists(_LOCK_FILE)


def acquire():
    if not _LOCK_FILE: return True
    _stale()
    try:
        fd = os.open(_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w') as f:
            json.dump({"started": time.time(), "total": 0, "done": 0, "date": "", "district": ""}, f)
        return True
    except OSError as e:
        if e.errno == errno.EEXIST:
            return False
        raise


def update_progress(total=None, done=None, date=None, district=None):
    if not _LOCK_FILE or not os.path.exists(_LOCK_FILE): return
    try:
        with open(_LOCK_FILE, 'r') as f: d = json.load(f)
    except: return
    if total is not None: d["total"] = total
    if done is not None: d["done"] = done
    if date is not None: d["date"] = date
    if district is not None: d["district"] = district
    with open(_LOCK_FILE, 'w') as f: json.dump(d, f)


def release():
    if _LOCK_FILE and os.path.exists(_LOCK_FILE):
        try: os.unlink(_LOCK_FILE)
        except: pass


def get_status():
    if not _LOCK_FILE or not os.path.exists(_LOCK_FILE):
        return {"running": False, "since": None, "total": 0, "done": 0, "date": "", "district": ""}
    mtime = os.path.getmtime(_LOCK_FILE)
    if time.time() - mtime > 1800:
        return {"running": False, "since": None, "total": 0, "done": 0, "date": "", "district": ""}
    from datetime import datetime
    try:
        with open(_LOCK_FILE, 'r') as f: d = json.load(f)
    except:
        d = {"started": mtime, "total": 0, "done": 0, "date": "", "district": ""}
    return {
        "running": True,
        "since": datetime.fromtimestamp(d.get("started", mtime)).strftime("%H:%M:%S"),
        "total": d.get("total", 0), "done": d.get("done", 0),
        "date": d.get("date", ""), "district": d.get("district", "")
    }
