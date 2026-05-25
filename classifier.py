"""
DeepSeek V4 Flash 分类器 — 批量模式，利用 1M 上下文，每次请求处理多条记录
并发上限限制 500（共享账号保护）
"""
import json, time, os, math
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = "deepseek-v4-flash"

# 限流：共享账号，最大并发 500，保险起见用 10
MAX_CONCURRENCY = 10
# 每批最多处理的记录数（~300 tok/条 × 30条 ≈ 9000 tok，远低于 1M）
BATCH_SIZE = 30

BATCH_SYSTEM = """你是政府采购商品分类专家。下面有一批商品清单，请逐一判断每个商品是否属于电脑硬件设备。

电脑设备（is_computer=true）：
- DT：台式机/台式整机/工作站/塔式服务器/组装机/兼容机/电脑主机/云桌面/云终端
- NB：笔记本电脑/便携式计算机/商务本/游戏本/移动工作站
- AIO：一体机/触控一体机/交互式一体机（无打印功能）

非电脑（is_computer=false）：
- 独立外设：显示器/投影仪/幕布
- 打印机类：激光/喷墨/打印复印扫描一体机
- 配件：键盘/鼠标/耳机/音箱/摄像头/U盘
- 耗材：硒鼓/墨盒/纸张/文具/纸质笔记本
- 其他：软件/服务/网络设备/家具/电器/医疗器械

只输出 JSON 数组，不要任何其他文字：
[{"id":序号, "is_computer":true/false, "category":"DT/NB/AIO或空", "brand":"品牌或空", "reason":"判据"}]"""

_client = None
_stats = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "errors": 0, "batched": 0}


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


def _build_batch_prompt(batch):
    """构建批量分类 prompt。batch = [(idx, record_dict), ...]"""
    lines = []
    for i, (idx, r) in enumerate(batch):
        lines.append(
            f"#{idx} 标题：{r.get('title','')} | "
            f"产品：{r.get('item_name','')} | "
            f"规格：{r.get('item_spec','')} | "
            f"目录：{r.get('gc_name','')}"
        )
    return "\n".join(lines)


def classify_batch(batch, max_retries=2):
    """分类一批记录，返回 [(record_id, is_computer, category, brand, reason), ...]"""
    client = _get_client()
    prompt = _build_batch_prompt(batch)
    ids = [r[0] for r in batch]  # record IDs

    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": BATCH_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                max_tokens=min(len(batch) * 120, 4096),
            )
            _stats["requests"] += 1
            _stats["batched"] += len(batch)
            if resp.usage:
                _stats["input_tokens"] += resp.usage.prompt_tokens or 0
                _stats["output_tokens"] += resp.usage.completion_tokens or 0

            text = resp.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("\n", 1)[0]
            results = json.loads(text)
            if not isinstance(results, list):
                results = [results]

            # 按 id 映射
            out = []
            for r in results:
                rid = r.get("id", 0)
                if rid in ids:
                    out.append((
                        rid,
                        1 if r.get("is_computer") else 0,
                        r.get("category", ""),
                        r.get("brand", ""),
                        r.get("reason", ""),
                    ))
            # 补回没被 LLM 返回的
            found_ids = {r.get("id") for r in results}
            for rid in ids:
                if rid not in found_ids:
                    out.append((rid, 0, "", "", "LLM未返回"))
            return out

        except json.JSONDecodeError:
            if attempt == max_retries - 1:
                # 尝试修复截断的 JSON：补全最后一个 ]
                fixed = text.rstrip()
                if fixed.endswith(","):
                    fixed = fixed[:-1]
                if not fixed.endswith("]"):
                    fixed += "]"
                try:
                    results = json.loads(fixed)
                    if isinstance(results, list):
                        out = []
                        found_ids = set()
                        for r in results:
                            rid = r.get("id", 0)
                            if rid in ids:
                                out.append((rid, 1 if r.get("is_computer") else 0,
                                           r.get("category",""), r.get("brand",""),
                                           r.get("reason","")))
                                found_ids.add(rid)
                        err_count = len(batch) - len(out)
                        _stats["errors"] += err_count
                        for rid in ids:
                            if rid not in found_ids:
                                out.append((rid, 0, "", "", "JSON truncated"))
                        return out
                except json.JSONDecodeError:
                    pass
                _stats["errors"] += len(batch)
                return [(r[0], 0, "", "", f"JSON bad: {text[:30]}") for r in batch]
            time.sleep(1)
        except Exception as e:
            if attempt == max_retries - 1:
                _stats["errors"] += len(batch)
                return [(r[0], 0, "", "", f"API err: {str(e)[:30]}") for r in batch]
            time.sleep(2 * (attempt + 1))


def classify_all(records, progress_callback=None):
    """将 records 分成 BATCH_SIZE 的批次，并发调用 DeepSeek"""
    total = len(records)
    # 构建批次
    batches = []
    for i in range(0, total, BATCH_SIZE):
        chunk = records[i:i + BATCH_SIZE]
        batches.append([(r["id"], r) for r in chunk])

    all_results = []
    done_batches = 0
    total_batches = len(batches)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures = {executor.submit(classify_batch, b): i for i, b in enumerate(batches)}
        for future in as_completed(futures):
            try:
                results = future.result()
                all_results.extend(results)
            except Exception:
                _stats["errors"] += BATCH_SIZE
            done_batches += 1
            if progress_callback:
                progress_callback(
                    min(done_batches * BATCH_SIZE, total),
                    total
                )

    return all_results


def get_stats():
    return dict(_stats)


import db


def run_classify(date_from=None, date_to=None, city=None, force=False, progress_callback=None):
    """从 PG 拉取待分类记录，批量调 DeepSeek，写回结果。force=True 时重新分类已分类记录。"""
    global _stats
    _stats = {"input_tokens": 0, "output_tokens": 0, "requests": 0, "errors": 0, "batched": 0}

    conn = db._conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT id, title, item_name, item_spec, gc_name FROM contracts WHERE is_deleted=0 AND is_computer=0"
            params = []
            if not force:
                sql += " AND match_keywords NOT LIKE %s"
                params.append("%[AI:%")  # 排除已分类
            if date_from:
                sql += " AND released_at >= %s"
                params.append(date_from)
            if date_to:
                sql += " AND released_at <= %s"
                params.append(date_to)
            if city:
                sql += " AND city_name=%s"
                params.append(city)
            sql += " ORDER BY id"
            cur.execute(sql, params)
            cols = ["id", "title", "item_name", "item_spec", "gc_name"]
            records = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        db._put(conn)

    if not records:
        return 0, 0, dict(_stats)

    total = len(records)
    estimated_batches = math.ceil(total / BATCH_SIZE)
    print(f"Classify: {total} records in ~{estimated_batches} batches, concurrency={MAX_CONCURRENCY}")
    results = classify_all(records, progress_callback=progress_callback)

    # 写回 PG
    conn = db._conn()
    updated = 0
    comp_count = 0
    try:
        with conn.cursor() as cur:
            for rid, is_comp, cat, brand, reason in results:
                cur.execute(
                    """UPDATE contracts SET is_computer=%s, category=%s, brand=%s,
                       mark_status=CASE WHEN %s=1 THEN 'starred' ELSE mark_status END,
                       match_keywords=%s WHERE id=%s""",
                    (is_comp, cat, brand, is_comp, f"[AI:{reason}]", rid),
                )
                if cur.rowcount > 0:
                    updated += 1
                    if is_comp:
                        comp_count += 1
            conn.commit()
    finally:
        db._put(conn)

    # 记入 token 日志
    db.log_classify(
        total_records=total, computer_count=comp_count,
        api_calls=_stats["requests"], input_tokens=_stats["input_tokens"],
        output_tokens=_stats["output_tokens"], errors=_stats["errors"],
        city_name=city or "全部"
    )
    return updated, comp_count, dict(_stats)


if __name__ == "__main__":
    import sys, config
    db.init_db(config.DB_HOST, config.DB_NAME, config.DB_USER, config.DB_PASSWORD, config.DB_PORT)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    city = sys.argv[2] if len(sys.argv) > 2 else None
    dfrom = sys.argv[3] if len(sys.argv) > 3 else None
    dto = sys.argv[4] if len(sys.argv) > 4 else None
    updated, comp, stats = run_classify(date_from=dfrom, date_to=dto, city=city,
                                         progress_callback=lambda d, t: print(f"\r  {d}/{t}", end="", flush=True))
    print(f"\nDone. Updated {updated} records, {comp} computer-related.")
    print(f"API calls: {stats['requests']} (batched {stats['batched']} records, ~{stats['batched']//max(1,stats['requests'])}/call)")
    print(f"Tokens: {stats['input_tokens']} in + {stats['output_tokens']} out")
    print(f"Errors: {stats['errors']}")
