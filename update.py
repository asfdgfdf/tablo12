#!/usr/bin/env python3
"""Оновлює data.json: ціни ОККО (okko.ua) і котирування London Gas Oil (investing.com).

Запускається GitHub Actions кожні ~10 хвилин. Якщо джерело недоступне,
залишає попередні значення й записує помилку в поле "errors".
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data.json"
URL_OKKO = "https://www.okko.ua/fuels"
TOKA_STATION_ID = 1180  # TOKA #2101 FOOD GARAGE Fast Charger (id на toka.energy/mapa)
URL_TOKA = "https://toka.energy/map/stations/{}"
URL_EV = "https://www.okko.ua/api/uk/fuel-map"
URL_EV_HTML = "https://www.okko.ua/fuel-map"
URL_OREE = "https://www.oree.com.ua/index.php/main/get_uah_prices"
URL_USD_DAY = "https://charts.finance.ua/ua/currency/data-daily?for=interbank&source=1&indicator=usd"
URL_USD_ARC = "https://charts.finance.ua/ua/currency/data-archive?for=interbank&source=1&indicator=usd"
URL_GAS = "https://ru.investing.com/commodities/london-gas-oil-streaming-chart"
HISTORY_POINTS = 300  # ≈ 2 доби при оновленні кожні 10 хв

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8,en;q=0.7",
}

FILL_TO_KEY = {
    "#9CFF00": "pulls100",
    "#F4F5F7": "pullsdp",
    "#FFEA00": "dp",
    "#F73C43": "pulls95",
    "#2EFAD5": "gas",
}
ORDER = ["pulls100", "pullsdp", "dp", "pulls95", "a95", "gas", "adblue"]
MONTHS = dict(F="Січ", G="Лют", H="Бер", J="Кві", K="Тра", M="Чер",
              N="Лип", Q="Сер", U="Вер", V="Жов", X="Лис", Z="Гру")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def parse_okko(html: str) -> dict:
    out = {}
    for idx, chunk in enumerate(c for c in html.split('<li class="item')
                                if re.search(r'class="price"', c)):
        m = re.search(r'class="price"[^>]*>\s*(\d+)\s*<sup[^>]*>\s*(\d+)\s*</sup>', chunk)
        if not m:
            continue
        price = float(f"{m.group(1)}.{m.group(2)}")
        fm = re.search(r'<svg[^>]*fill="(#[0-9A-Fa-f]{6})"[^>]*class="mask"', chunk)
        fill = fm.group(1).upper() if fm else ""
        lm = re.search(r"<text[^>]*>\s*([^<]+?)\s*</text>", chunk)
        label = lm.group(1) if lm else ""
        key = FILL_TO_KEY.get(fill)
        if fill == "#1EACFF":
            key = "adblue" if "adblue" in label.lower() else "a95"
        if not key and idx < len(ORDER):
            key = ORDER[idx]
        if key and key not in out and 10 < price < 500:
            out[key] = price
    if len(out) < 3:
        raise ValueError("не знайдено цін на сторінці")
    return out


def _find_collection(o):
    if isinstance(o, dict):
        if isinstance(o.get("collection"), list):
            return o["collection"]
        for v in o.values():
            r = _find_collection(v)
            if r is not None:
                return r
    return None


def parse_ev(text: str) -> dict:
    """Ціна зарядки на порті CCS 2 з карти АЗК ОККО (грн/кВт·год) і кількість вільних портів."""
    col = _find_collection(json.loads(text)) or []
    prices, free, total = [], 0, 0
    rx = re.compile(r"CCS[^–\-]*[–\-]\s*(\d+)\s*кВт.*?Ціна:\s*([\d.,]+)\s*грн/кВт.*?Статус:\s*([^\s.<|]+)", re.S)
    for st in col:
        stations = (st.get("attributes") or {}).get("stations") or {}
        for ports in stations.values():
            for c in ports or []:
                if c.get("code") != "CCS_2":
                    continue
                txt = re.sub(r"<[^>]+>", " ", c.get("value", ""))
                for m in rx.finditer(txt):
                    prices.append(float(m.group(2).replace(",", ".")))
                    total += 1
                    free += m.group(3).lower().startswith("вільн")
    if not prices:
        raise ValueError("не знайдено цін CCS 2")
    mode = max(set(prices), key=prices.count)
    return {"price": mode, "min": min(prices), "max": max(prices), "ports": total, "free": free}


def oree_post(data: dict) -> str:
    import urllib.parse
    req = urllib.request.Request(URL_OREE, data=urllib.parse.urlencode(data).encode(),
                                 headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded",
                                          "X-Requested-With": "XMLHttpRequest"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("cp1251", "replace")


def parse_oree(html: str, kind: str) -> list:
    """Індекси РДН з oree.com.ua: [{label, base, pct}] — як на головній сторінці."""
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).replace("&nbsp;", " ")
    pat = r"\b\d{2}\.\d{2}\.\d{4}\b" if kind == "day" else r"\b\d{2}\.\d{4}\b"
    labels = re.findall(pat, txt.split("BASE")[0])
    vals = re.findall(r"BASE\s+([\d.]+)[^%]*?(-?[\d.]+)\s*%", txt)
    out = []
    for lab, (v, pct) in zip(labels, vals):
        out.append({"label": lab, "base": float(v), "pct": float(pct)})
    if not out:
        raise ValueError("не знайдено індексів BASE")
    return out


def fetch_oree(now) -> dict:
    from datetime import timedelta
    month = now.strftime("%m.%Y")
    days = None
    for d in (now + timedelta(days=1), now):          # завтра (якщо РДН уже відторгувався) або сьогодні
        ds = d.strftime("%d.%m.%Y")
        res = parse_oree(oree_post({"day": ds, "month": month, "type": "day"}), "day")
        if res and res[-1]["label"] == ds:
            days = res
            break
    if days is None:
        raise ValueError("немає даних РДН за добу")
    months = parse_oree(oree_post({"day": now.strftime("%d.%m.%Y"), "month": month, "type": "month"}), "month")
    # прибрати дублікати місяців (сайт іноді повторює поточний)
    seen, uniq = set(), []
    for m in months:
        if m["label"] not in seen:
            seen.add(m["label"]); uniq.append(m)
    return {"days": days, "months": uniq}


def fetch_usd() -> dict:
    """Міжбанк USD/UAH (Укрділінг) з charts.finance.ua: поточний курс і останні дні."""
    intraday = json.loads(fetch(URL_USD_DAY))
    archive = json.loads(fetch(URL_USD_ARC))
    def d(s):  # "10/07/2026" -> "07.10.2026"
        m, dd, y = s[:10].split("/")
        return f"{dd}.{m}.{y}"
    days = [{"date": d(r[0]), "buy": float(r[1]), "sell": float(r[2])} for r in archive[-6:]]
    if intraday:
        last = intraday[-1]
        cur = {"date": d(last[0]), "time": last[0][11:16], "buy": float(last[1]), "sell": float(last[2]),
               "open_buy": float(intraday[0][1]), "open_sell": float(intraday[0][2])}
    else:
        a = archive[-1]
        cur = {"date": d(a[0]), "time": None, "buy": float(a[1]), "sell": float(a[2])}
    prev = [x for x in days if x["date"] != cur["date"]]
    if prev:
        cur["prev_buy"], cur["prev_sell"], cur["prev_date"] = prev[-1]["buy"], prev[-1]["sell"], prev[-1]["date"]
    if not (10 < cur["sell"] < 200):
        raise ValueError(f"неправдоподібний курс {cur['sell']}")
    return {"now": cur, "days": [x for x in days if x["date"] != cur["date"]][-5:]}


def parse_ev_html(html: str) -> dict:
    """Запасний варіант: ціни CCS 2 з HTML сторінки карти (дані вбудовані в сторінку)."""
    t = html.replace("\\u002F", "/").replace("\\u003C", "<").replace("\\u003E", ">")
    rx = re.compile(r"CCS[^–\-<]*[–\-]\s*(\d+)\s*кВт[^<]*?Ціна:\s*([\d.,]+)\s*грн/кВт[^<]*?Статус:\s*([^\s.<|]+)")
    prices, free = [], 0
    for m in rx.finditer(t):
        prices.append(float(m.group(2).replace(",", ".")))
        free += m.group(3).lower().startswith("вільн")
    if not prices:
        raise ValueError("не знайдено цін CCS 2")
    mode = max(set(prices), key=prices.count)
    return {"price": mode, "min": min(prices), "max": max(prices), "ports": len(prices), "free": free}


def fetch_toka() -> dict:
    """Ціна CCS 2 на станції TOKA #2101 FOOD GARAGE з toka.energy/mapa."""
    d = json.loads(fetch(URL_TOKA.format(TOKA_STATION_ID)))
    ports = [p for p in d.get("ports", []) if "CCS" in (p.get("title") or "").upper()]
    if not ports:
        raise ValueError("на станції немає порту CCS 2")
    p = ports[0]
    return {"price": float(p["price"]), "power": p.get("power"), "status": p.get("status"),
            "station": d.get("name"), "address": d.get("address")}


def ru_num(s: str) -> float:
    s = s.replace("−", "-").replace(".", "").replace(",", ".")
    return float(re.sub(r"[^\d.\-+]", "", s))


def parse_gasoil(html: str) -> dict:
    h = html.replace('\\"', '"')
    r = {}
    m = re.search(r'"commodityStore":\{"instrument":\{.*?"price":(\{[^{}]*\})', h, re.S)
    if m:
        p = json.loads(m.group(1))
        r = {"last": p["last"], "change": p.get("change", 0), "change_pct": p.get("changePcr", 0),
             "prev_close": p.get("lastClose"), "day_low": p.get("low"), "day_high": p.get("high")}
        if p.get("lastUpdateTime"):
            ts = datetime.fromtimestamp(int(p["lastUpdateTime"]) / 1000, tz=timezone.utc)
            r["quote_time"] = ts.astimezone(KYIV).strftime("%H:%M:%S")
    else:
        lm = re.search(r'data-test="instrument-price-last"[^>]*>([^<]+)<', h)
        if not lm:
            raise ValueError("не знайдено котирування на сторінці")
        cm = re.search(r'data-test="instrument-price-change"[^>]*>([^<]+)<', h)
        pm = re.search(r'data-test="instrument-price-change-percent"[^>]*>\(?([^<%]+)%', h)
        last = ru_num(lm.group(1))
        chg = ru_num(cm.group(1)) if cm else 0.0
        r = {"last": last, "change": chg, "change_pct": ru_num(pm.group(1)) if pm else 0.0,
             "prev_close": round(last - chg, 2), "day_low": None, "day_high": None}
    km = re.search(r"\(([A-Z]{2,4})([FGHJKMNQUVXZ])(\d{1,2})\)\s*</h1>", h)
    if km:
        yy = km.group(3) if len(km.group(3)) == 2 else "2" + km.group(3)
        r["contract"] = f"{MONTHS[km.group(2)]} '{yy} ({km.group(1)}{km.group(2)}{km.group(3)})"
    if not (100 < float(r["last"]) < 5000):
        raise ValueError(f"неправдоподібна ціна {r['last']}")
    return r


TV_URL = ("https://scanner.tradingview.com/symbol?symbol=ICEEUR:ULS1!"
          "&fields=close,change,change_abs,high,low,description,update_time,expiration")
EN_MONTHS = dict(Jan="F", Feb="G", Mar="H", Apr="J", May="K", Jun="M", Jul="N",
                 Aug="Q", Sep="U", Oct="V", Nov="X", Dec="Z")


def gasoil_tradingview() -> dict:
    """Запасне джерело: TradingView, ICE Low Sulphur Gasoil, найближчий контракт (ULS1!)."""
    p = json.loads(fetch(TV_URL))
    last = float(p["close"])
    chg = float(p.get("change_abs") or 0)
    r = {"last": last, "change": chg, "change_pct": round(float(p.get("change") or 0), 2),
         "prev_close": round(last - chg, 2), "day_low": p.get("low"), "day_high": p.get("high")}
    if p.get("update_time"):
        ts = datetime.fromtimestamp(int(p["update_time"]), tz=timezone.utc)
        r["quote_time"] = ts.astimezone(KYIV).strftime("%H:%M:%S")
    m = re.search(r"\((\w{3}) (\d{4})\)", p.get("description", ""))
    if m and m.group(1) in EN_MONTHS:
        code = EN_MONTHS[m.group(1)]
        r["contract"] = f"{MONTHS[code]} '{m.group(2)[2:]} (LGO{code}{m.group(2)[3]})"
    else:
        r["contract"] = "найближчий контракт"
    if not (100 < last < 5000):
        raise ValueError(f"неправдоподібна ціна {last}")
    return r


def main() -> int:
    old = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
    now = datetime.now(KYIV).replace(microsecond=0)
    new = dict(old)
    new["checked_at"] = now.isoformat()
    errors = {}

    try:
        fuels = parse_okko(fetch(URL_OKKO))
        if old.get("fuels") and any(abs(fuels.get(k, 0) - v) > 0.001 for k, v in old["fuels"].items()):
            new["fuels_prev"] = old["fuels"]
            new["fuels_changed_at"] = now.isoformat()
        new["fuels"] = {**old.get("fuels", {}), **fuels}
        new["fuels_at"] = now.isoformat()
    except Exception as e:  # noqa: BLE001
        errors["okko"] = str(e)[:200]

    try:
        ev = fetch_toka()
        old_ccs = (old.get("fuels") or {}).get("ccs2")
        if old_ccs is not None and abs(old_ccs - ev["price"]) > 0.001:
            new["fuels_prev"] = {**(new.get("fuels_prev") or old.get("fuels") or {}), "ccs2": old_ccs}
        new["fuels"] = {**new.get("fuels", {}), "ccs2": ev["price"]}
        new["ev"] = ev
    except Exception as e:  # noqa: BLE001
        errors["ev"] = str(e)[:200]

    try:
        try:
            gas = parse_gasoil(fetch(URL_GAS))
            new["gasoil_source"] = "ru.investing.com"
        except Exception as e1:  # noqa: BLE001
            print(f"investing.com: {e1}; пробую TradingView")
            gas = gasoil_tradingview()
            new["gasoil_source"] = "TradingView (ICE)"
        new["gasoil"] = {**old.get("gasoil", {}), **{k: v for k, v in gas.items() if v is not None}}
        new["gasoil_at"] = now.isoformat()
        hist = old.get("history", [])
        hist.append({"t": now.isoformat(), "v": gas["last"]})
        new["history"] = hist[-HISTORY_POINTS:]
    except Exception as e:  # noqa: BLE001
        errors["gasoil"] = str(e)[:200]

    try:
        new["dam"] = fetch_oree(now)
        new["dam_at"] = now.isoformat()
    except Exception as e:  # noqa: BLE001
        errors["oree"] = str(e)[:200]

    try:
        new["usd"] = fetch_usd()
        new["usd_at"] = now.isoformat()
    except Exception as e:  # noqa: BLE001
        errors["usd"] = str(e)[:200]

    new["errors"] = errors
    DATA.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"errors": errors, "gasoil": new.get("gasoil", {}).get("last"),
                      "fuels": new.get("fuels")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
