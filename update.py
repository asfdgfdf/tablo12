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

    new["errors"] = errors
    DATA.write_text(json.dumps(new, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"errors": errors, "gasoil": new.get("gasoil", {}).get("last"),
                      "fuels": new.get("fuels")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
