"""
采集模块 — jxemall API → content HTML 解析 → 结构化字段
"""
import json, re, time, ssl
from datetime import datetime, timedelta
import requests
from requests.adapters import HTTPAdapter

from config import (
    API_QUERY_URL, ANNOUNCEMENT_DETAIL_URL,
    CONTRACT_ANNOUNCEMENT_TYPES, TARGET_DISTRICTS,
    COMPUTER_KEYWORDS_POSITIVE, COMPUTER_KEYWORDS_EXCLUDE,
)

# ====== SSL 兼容 ======

class _LegacySSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
        except AttributeError:
            pass
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

_session = requests.Session()
_session.mount('https://', _LegacySSLAdapter())

# ====== API 请求 ======

def fetch_page(district_code, start_date, end_date, page_no=1, page_size=50):
    """请求一页合同公告数据"""
    payload = {
        "pageSize": page_size,
        "pageNo": page_no,
        "announcementTypes": CONTRACT_ANNOUNCEMENT_TYPES,
        "excludeTagCodes": ["contractRevocation"],
        "district": district_code,
        "all": {"blackList": [], "whiteList": []},
        "startDate": f"{start_date} 00:00:00",
        "endDate": f"{end_date} 23:59:59",
    }
    resp = _session.post(API_QUERY_URL, json=payload,
                         headers={"Content-Type": "application/json",
                                  "User-Agent": "Mozilla/5.0 (JXEMallCollector/1.0)"},
                         timeout=30)
    data = resp.json()
    if data.get("success") and data.get("result"):
        return data["result"].get("data", []), data["result"].get("total", 0)
    return [], 0


def _strip_html(html):
    """简易 HTML 标签剥离 + 实体解码"""
    import html as _html_mod
    text = re.sub(r'<[^>]+>', '', html)
    text = _html_mod.unescape(text)
    return text


# ====== Content HTML 解析 ======

def parse_content(record):
    """从单条 API 记录中解析所有结构化字段。
    返回 (meta_dict, line_items_list)，meta 为合同级字段，line_items 为合同内容表行（可能有0-N行）。
    """
    title = record.get("title", "")
    district = record.get("district", "")
    district_name = record.get("districtName", "")
    encrypt_id = record.get("encryptId", "")
    released_at = record.get("releasedAt")
    announcement_type = record.get("announcementTypeName", "")
    project_name = record.get("projectName", "")
    project_code = record.get("projectCode", "")
    serial_num = record.get("serialNum", "")
    serial_type = record.get("serialType", "")
    meta_data = record.get("metaData", "{}")
    content = record.get("content", "") or ""

    # 发布时间格式化
    pub_date = ""
    if released_at:
        try:
            pub_date = datetime.fromtimestamp(released_at / 1000).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pub_date = str(released_at)

    # 详情页 URL
    detail_url = ""
    if encrypt_id and district:
        detail_url = f"{ANNOUNCEMENT_DETAIL_URL}?encryptId={encrypt_id}&district={district}"

    # 解析 metaData JSON
    gc_name = ""
    try:
        md = json.loads(meta_data)
        gc_name = md.get("gpCatalogName", "")
    except (json.JSONDecodeError, TypeError):
        pass

    # 从 content HTML 提取字段
    plain = _strip_html(content)

    # 采购人名称
    buyer = ""
    m = re.search(r'一[、.]\s*采购人名称[：:]\s*(.+?)(?:二[、.]|$)', plain, re.DOTALL)
    if not m:
        m = re.search(r'采购人名称[：:]\s*(.+?)(?:\n|供应商|二)', plain)
    if m: buyer = m.group(1).strip()

    # 供应商名称
    supplier = ""
    m = re.search(r'二[、.]\s*供应商名称[：:]\s*(.+?)(?:三[、.]|$)', plain, re.DOTALL)
    if not m:
        m = re.search(r'供应商名称[：:]\s*(.+?)(?:\n|采购项目|三)', plain)
    if m: supplier = m.group(1).strip()

    # 合同编号
    contract_no = ""
    m = re.search(r'五[、.]\s*合同编号[：:]\s*(.+?)(?:六[、.]|$)', plain, re.DOTALL)
    if not m:
        m = re.search(r'合同编号[：:]\s*(.+?)(?:\n|合同内容|六)', plain)
    if m: contract_no = m.group(1).strip()

    # 附件链接
    attachments = []
    for m in re.finditer(r'<a\s+href="([^"]+)"[^>]*>([^<]+)</a>', content):
        href, label = m.group(1), m.group(2).strip()
        if href and label:
            attachments.append(f"{label}|{href}")

    # 解析合同内容表
    # 表头行
    table_headers = ["序号", "标项名称", "规格型号", "单位", "数量", "单价(元)", "总价(元)"]
    line_items = []

    # 匹配 <table> 中的每一行（跳过表头）
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', content, re.DOTALL)
    found_header = False
    for row_html in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
        if not cells: continue
        cell_texts = [_strip_html(c).strip() for c in cells]

        # 检测表头行
        if not found_header:
            if any(h in "".join(cell_texts) for h in ["标项名称", "规格型号", "序号"]):
                found_header = True
                continue

        # 数据行（至少3列有意义数据）
        if found_header and len(cell_texts) >= 3:
            # 跳过空行或合并单元格行
            if all(not c for c in cell_texts): continue
            item = {
                "idx": cell_texts[0] if len(cell_texts) > 0 else "",
                "item_name": cell_texts[1] if len(cell_texts) > 1 else "",
                "item_spec": cell_texts[2] if len(cell_texts) > 2 else "",
                "item_unit": cell_texts[3] if len(cell_texts) > 3 else "",
                "item_qty": _parse_float(cell_texts[4]) if len(cell_texts) > 4 else 0,
                "item_unit_price": _parse_float(cell_texts[5]) if len(cell_texts) > 5 else 0,
                "item_total_price": _parse_float(cell_texts[6]) if len(cell_texts) > 6 else 0,
            }
            line_items.append(item)

    meta = {
        "serial_num": serial_num,
        "serial_type": serial_type,
        "title": title,
        "contract_no": contract_no,
        "buyer": buyer,
        "supplier": supplier,
        "project_name": project_name,
        "project_code": project_code,
        "district_code": district,
        "district_name": district_name,
        "released_at": pub_date,
        "announcement_type": announcement_type,
        "detail_url": detail_url,
        "gc_name": gc_name,
        "raw_content": content,
        "attachments": "\n".join(attachments) if attachments else "",
    }
    return meta, line_items


def _parse_float(s):
    try: return float(s.strip().replace(",", ""))
    except (ValueError, AttributeError): return 0


# ====== 电脑产品分类 ======

def classify_computer(title, item_name):
    """关键词规则已废止，筛选由 DeepSeek LLM 在采集后完成。
    此函数保留接口兼容性，始终返回 is_computer=0。"""
    return False, "", ""


def infer_brand(item_name, item_spec):
    """从产品名称/规格推断品牌"""
    text = f"{item_name} {item_spec}"
    # 常见品牌
    brands = [
        "联想", "lenovo", "Lenovo", "华为", "HUAWEI", "Huawei",
        "戴尔", "Dell", "DELL", "惠普", "HP", "hp",
        "华硕", "ASUS", "宏碁", "Acer", "acer",
        "清华同方", "同方", "方正", "Founder",
        "神舟", "Hasee", "海尔", "Haier",
        "中科曙光", "曙光", "Sugon",
        "浪潮", "Inspur", "inspur",
        "长城", "GreatWall", "greatwall",
        "小米", "Xiaomi", "Redmi", "redmi",
        "荣耀", "Honor", "honor",
        "微软", "Microsoft", "Surface",
        "苹果", "Apple", "MacBook",
    ]
    text_lower = text.lower()
    for b in brands:
        if b.lower() in text_lower:
            return b.title()
    return ""


# ====== 批量采集 ======

def collect_range(date_from, date_to, cities=None, max_pages_per_district=None):
    """遍历指定城市的区县，分页抓取合同公告。
    cities: 城市名列表，None=全部7城。
    max_pages_per_district: 每个区县最多抓几页，None=不限。
    返回 records 列表，每条为展平后的单行记录。
    """
    import lock

    all_districts = []
    if cities:
        for cn in cities:
            if cn in TARGET_DISTRICTS:
                for code, name in TARGET_DISTRICTS[cn]["districts"].items():
                    all_districts.append((code, name, cn))
    else:
        for cn, cdata in TARGET_DISTRICTS.items():
            for code, name in cdata["districts"].items():
                all_districts.append((code, name, cn))

    total_districts = len(all_districts)
    all_records = []

    for idx, (dcode, dname, cname) in enumerate(all_districts):
        lock.update_progress(total=total_districts, done=idx + 1,
                             date=date_from, district=f"{cname}-{dname}")

        page_no = 1
        while True:
            try:
                data, total = fetch_page(dcode, date_from, date_to, page_no=page_no)
            except Exception as e:
                print(f"  [err] {cname}/{dname} page={page_no}: {e}")
                break

            if not data:
                break

            for rec in data:
                meta, items = parse_content(rec)

                if not items:
                    # 无合同内容表，仍保存一条基础记录
                    is_comp, kw_str, cat = classify_computer(meta["title"], "")
                    brand = ""
                    all_records.append({
                        **meta,
                        "city_name": cname,
                        "item_name": "",
                        "item_spec": "",
                        "item_unit": "",
                        "item_qty": 0,
                        "item_unit_price": 0,
                        "item_total_price": 0,
                        "is_computer": 1 if is_comp else 0,
                        "match_keywords": kw_str,
                        "category": cat,
                        "brand": brand,
                    })
                else:
                    for item in items:
                        is_comp, kw_str, cat = classify_computer(
                            meta["title"], item.get("item_name", ""))
                        brand = infer_brand(
                            item.get("item_name", ""), item.get("item_spec", ""))
                        all_records.append({
                            **meta,
                            "city_name": cname,
                            "item_name": item.get("item_name", ""),
                            "item_spec": item.get("item_spec", ""),
                            "item_unit": item.get("item_unit", ""),
                            "item_qty": item.get("item_qty", 0),
                            "item_unit_price": item.get("item_unit_price", 0),
                            "item_total_price": item.get("item_total_price", 0),
                            "is_computer": 1 if is_comp else 0,
                            "match_keywords": kw_str,
                            "category": cat,
                            "brand": brand,
                        })

            # 判断是否还有下一页
            if len(data) < 50:
                break
            if max_pages_per_district and page_no >= max_pages_per_district:
                break
            page_no += 1
            time.sleep(0.3)  # 请求间隔

        time.sleep(0.2)

    return all_records


if __name__ == "__main__":
    import sys
    dfrom = sys.argv[1] if len(sys.argv) > 1 else "2026-05-20"
    dto = sys.argv[2] if len(sys.argv) > 2 else dfrom
    recs = collect_range(dfrom, dto)
    print(f"\nTotal: {len(recs)} records")
    if recs:
        comp = sum(1 for r in recs if r["is_computer"])
        print(f"Computer-related: {comp}")
        cities_count = {}
        for r in recs:
            cities_count[r["city_name"]] = cities_count.get(r["city_name"], 0) + 1
        for cn, cnt in sorted(cities_count.items()):
            print(f"  {cn}: {cnt}")
