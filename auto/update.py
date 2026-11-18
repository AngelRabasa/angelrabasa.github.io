"""4 Números · actualizador automático.

Modos:  prices (cada día laborable: solo cotizaciones, 16 peticiones)
        filings (cada sábado: detecta cuentas nuevas y recalcula solo esas empresas)
        full    (manual: refresco completo por rotación, como antes)

Descarga los estados financieros de cada empresa (Alpha Vantage), calcula los
cuatro números y escribe ../../data.js, que lee la página 4-numeros.html.

Uso local:   ALPHAVANTAGE_KEY=tu_clave python3 update.py
Con GitHub Actions: ver .github/workflows/actualizar.yml
"""
import json, os, sys, time, datetime, urllib.request, urllib.parse, pathlib

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "data.js"
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)

# Cada empresa gasta 4 peticiones. El plan gratuito da 25 al día: máximo 6 por ejecución.
# Con más de 6 empresas, cada lunes se actualizan las 6 más antiguas y el resto se conserva.
# (símbolo, nombre, sector, región). Los de fuera de EE. UU. se piden como ADR (cotizan en USD)
# y sus estados se convierten a USD con el tipo de cambio del día.
COMPANIES = [
    ("KO",   "Coca-Cola",         "Consumer staples", "United States"),
    ("MSFT", "Microsoft",         "Technology",       "United States"),
    ("JNJ",  "Johnson & Johnson", "Healthcare",       "United States"),
    ("XOM",  "Exxon Mobil",       "Energy",           "United States"),
    ("WMT",  "Walmart",           "Retail",           "United States"),
    ("NKE",  "Nike",              "Consumer discretionary", "United States"),
    ("V",    "Visa",              "Payments",         "United States"),
    ("ASML", "ASML",              "Technology",       "Netherlands"),
    ("NVO",  "Novo Nordisk",      "Healthcare",       "Denmark"),
    ("BABA", "Alibaba",           "E-commerce",       "China"),
    ("SONY", "Sony",              "Entertainment & electronics", "Japan"),
    ("SAP",  "SAP",               "Technology",       "Germany"),
    ("SHEL", "Shell",             "Energy",           "United Kingdom"),
    ("NVS",  "Novartis",          "Healthcare",       "Switzerland"),
    ("TSM",  "TSMC",              "Technology",       "Taiwan"),
    ("INFY", "Infosys",           "Technology",       "India"),
]
BUDGET = 24   # peticiones por ejecución (límite gratuito: 25 al día). EE. UU. cuesta 4; el resto 5 (tipo de cambio)
GROWTH, DISCOUNT, TERMINAL = 0.05, 0.09, 0.02   # supuestos del valor intrínseco
SOURCE = "Alpha Vantage"


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def get(function, symbol, key):
    p = CACHE / f"{symbol}_{function}.json"
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
        {"function": function, "symbol": symbol, "apikey": key})
    for _ in range(3):
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        if "annualReports" in d or "Global Quote" in d:
            p.write_text(json.dumps(d))
            time.sleep(1.2)          # límite: 1 petición por segundo
            return d
        time.sleep(15)
    if p.exists():                   # si falla, usa la última descarga buena
        return json.loads(p.read_text())
    raise RuntimeError(f"{symbol} {function}: {str(d)[:120]}")


def get_fx(cur, key):
    if cur == "USD":
        return 1.0
    p = CACHE / f"FX_{cur}.json"
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
        {"function": "CURRENCY_EXCHANGE_RATE", "from_currency": cur, "to_currency": "USD", "apikey": key})
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
        rate = float(d["Realtime Currency Exchange Rate"]["5. Exchange Rate"])
        p.write_text(json.dumps({"rate": rate}))
        time.sleep(1.2)
        return rate
    except Exception:
        if p.exists():
            return json.loads(p.read_text())["rate"]
        raise


def dcf(fcf0, shares):
    if not fcf0 or fcf0 <= 0 or not shares:
        return None
    pv = sum(fcf0 * (1 + GROWTH) ** t / (1 + DISCOUNT) ** t for t in range(1, 11))
    tv = fcf0 * (1 + GROWTH) ** 10 * (1 + TERMINAL) / (DISCOUNT - TERMINAL) / (1 + DISCOUNT) ** 10
    return (pv + tv) / shares


def compute(sym, name, sector, inc, bal, cfs, quote, fx=1.0, ratio=1, region="United States"):
    # fx: moneda de los estados -> USD (el ADR cotiza en USD). ratio: acciones de los estados por ADR (Alpha Vantage ya da las acciones en equivalentes de ADR, así que casi siempre 1).
    I, B, C = inc["annualReports"], bal["annualReports"], cfs["annualReports"]
    n = min(5, len(I), len(B) - 1, len(C))
    hist = []
    for k in range(n):
        rev, gp, ni = f(I[k]["totalRevenue"]), f(I[k]["grossProfit"]), f(I[k]["netIncome"])
        eq0, eq1 = f(B[k]["totalShareholderEquity"]), f(B[k + 1]["totalShareholderEquity"])
        cfo, capex = f(C[k]["operatingCashflow"]), f(C[k]["capitalExpenditures"])
        fcf = cfo - abs(capex) if cfo is not None and capex is not None else None
        hist.append(dict(
            year=I[k]["fiscalDateEnding"][:4],
            revenue=rev * fx if rev else rev, fcf=fcf * fx if fcf is not None else None,
            gross=gp / rev if gp and rev else None,
            net=ni / rev if ni is not None and rev else None,
            roe=ni / ((eq0 + eq1) / 2) if ni is not None and eq0 and eq1 and (eq0 + eq1) > 0 else None,
            fcf_margin=fcf / rev if fcf is not None and rev else None))
    hist.reverse()
    last = hist[-1]
    shares = f(B[0].get("commonStockSharesOutstanding"))
    price = f(quote["Global Quote"]["05. price"])
    ads = shares / ratio if shares else None      # ADRs equivalentes
    mcap = price * ads if price and ads else None
    fc = [h["fcf"] for h in hist[-3:] if h["fcf"] is not None]
    value = dcf(sum(fc) / len(fc) if fc else None, ads)   # fc ya está en USD
    return dict(
        symbol=sym, name=name, sector=sector, currency="USD", reported_currency=I[0].get("reportedCurrency", "USD"), fx=fx, region=region, adr_ratio=ratio,
        fiscal_year=I[0]["fiscalDateEnding"], price=price,
        price_date=quote["Global Quote"]["07. latest trading day"],
        change_pct=f(str(quote["Global Quote"].get("10. change percent", "")).rstrip("%")),
        shares_m=ads / 1e6 if ads else None,
        market_cap_b=mcap / 1e9 if mcap else None,
        fcf_yield=last["fcf"] / mcap if mcap and last["fcf"] is not None else None,
        value_per_share=value,
        price_vs_value=price / value - 1 if value and price else None,
        history=hist)


def full():
    key = os.environ.get("ALPHAVANTAGE_KEY")
    if not key:
        sys.exit("Falta ALPHAVANTAGE_KEY")
    old = {}
    if OUT.exists():
        try:
            old = {c["symbol"]: c for c in json.loads(OUT.read_text()[len("window.N4="):].strip().rstrip(";"))["companies"]}
        except Exception:
            old = {}
    order = sorted(COMPANIES, key=lambda c: old.get(c[0], {}).get("price_date", ""))
    todo, used = set(), 0
    for c in order:
        cost = 4 if c[3] == "United States" else 5   # (algunos ADR informan en USD y cuestan 4)
        if used + cost <= BUDGET:
            todo.add(c[0]); used += cost
    out = []
    for sym, name, sector, region in COMPANIES:
        if sym not in todo and sym in old:
            out.append(old[sym]); print("=  ", sym, "(sin cambios, le toca otra semana)"); continue
        try:
            inc = get("INCOME_STATEMENT", sym, key)
            fx = get_fx(inc["annualReports"][0].get("reportedCurrency", "USD"), key)
            r = compute(sym, name, sector, inc, get("BALANCE_SHEET", sym, key),
                        get("CASH_FLOW", sym, key), get("GLOBAL_QUOTE", sym, key), fx, 1, region)
            out.append(r)
            print("OK ", sym)
        except Exception as e:
            print("FALLO", sym, e)
            if sym in old:
                out.append(old[sym])
    payload = dict(updated=datetime.date.today().isoformat(), source=SOURCE,
                   assumptions=dict(growth=GROWTH, discount=DISCOUNT, terminal=TERMINAL),
                   companies=out)
    OUT.write_text("window.N4=" + json.dumps(payload, ensure_ascii=False) + ";\n")


STATE = HERE / "state.json"
CHECK_COST_FULL = 3          # BALANCE_SHEET + CASH_FLOW + GLOBAL_QUOTE (+1 si hay que convertir moneda)


def load_data():
    return json.loads(OUT.read_text()[len("window.N4="):].strip().rstrip(";"))


def save_data(payload, before):
    """Escribe data.js solo si pasa las comprobaciones de cordura."""
    syms_before = {c["symbol"] for c in before["companies"]}
    syms_after = {c["symbol"] for c in payload["companies"]}
    if syms_before - syms_after:
        sys.exit(f"ABORTO: desaparecen empresas {syms_before - syms_after}")
    for c in payload["companies"]:
        if not c.get("price") or c["price"] <= 0:
            sys.exit(f"ABORTO: precio no válido en {c['symbol']}")
    text = "window.N4=" + json.dumps(payload, ensure_ascii=False) + ";\n"
    if OUT.exists() and OUT.read_text() == text:
        print("Sin cambios."); return
    OUT.write_text(text)
    print("data.js actualizado.")


def quote_only(sym, key):
    url = "https://www.alphavantage.co/query?" + urllib.parse.urlencode(
        {"function": "GLOBAL_QUOTE", "symbol": sym, "apikey": key})
    with urllib.request.urlopen(url, timeout=60) as r:
        d = json.load(r)
    time.sleep(1.2)
    q = d.get("Global Quote") or {}
    if not q.get("05. price"):
        raise RuntimeError(str(d)[:120])
    return q


def apply_price(c, q):
    price = f(q["05. price"])
    if not price or price <= 0:
        raise ValueError("precio no válido")
    c["price"] = price
    c["price_date"] = q.get("07. latest trading day", c.get("price_date"))
    ch = f(str(q.get("10. change percent", "")).rstrip("%"))
    if ch is not None:
        c["change_pct"] = ch
    mcap = price * c["shares_m"] * 1e6 if c.get("shares_m") else None
    c["market_cap_b"] = mcap / 1e9 if mcap else None
    last = c["history"][-1].get("fcf") if c.get("history") else None
    c["fcf_yield"] = last / mcap if mcap and last is not None else None
    v = c.get("value_per_share")
    c["price_vs_value"] = price / v - 1 if v else None


def prices():
    """Cada día laborable: actualiza solo la cotización (16 peticiones, límite gratuito 25)."""
    key = os.environ.get("ALPHAVANTAGE_KEY") or sys.exit("Falta ALPHAVANTAGE_KEY")
    data = load_data()
    before = json.loads(json.dumps(data))
    ok = fail = 0
    for c in data["companies"]:
        try:
            apply_price(c, quote_only(c["symbol"], key)); ok += 1
        except Exception as e:
            fail += 1; print("FALLO", c["symbol"], str(e)[:100])
            if "rate limit" in str(e).lower() or "per day" in str(e).lower():
                print("Límite diario alcanzado; se conserva el resto."); break
    print(f"cotizaciones: {ok} ok, {fail} fallos")
    if ok == 0:
        sys.exit("Ninguna cotización actualizada (¿festivo o límite de la API?).")
    data["updated"] = datetime.date.today().isoformat()
    save_data(data, before)


def filings():
    """Cada sábado: mira si hay cuentas anuales nuevas (1 petición por empresa) y,
    solo si las hay, recalcula esa empresa entera con el mismo modelo de siempre."""
    key = os.environ.get("ALPHAVANTAGE_KEY") or sys.exit("Falta ALPHAVANTAGE_KEY")
    data = load_data()
    before = json.loads(json.dumps(data))
    try:
        state = json.loads(STATE.read_text())
    except Exception:
        state = {}
    meta = {c[0]: c for c in COMPANIES}
    by = {c["symbol"]: c for c in data["companies"]}
    order = sorted(by, key=lambda s: state.get(s, ""))     # primero las comprobadas hace más tiempo
    used = 0
    today = datetime.date.today().isoformat()
    for sym in order:
        if sym not in meta:
            continue
        if used + 1 > BUDGET:
            print("Presupuesto agotado; el resto se comprueba el próximo sábado."); break
        try:
            inc = get("INCOME_STATEMENT", sym, key); used += 1
            newest = inc["annualReports"][0]["fiscalDateEnding"]
            state[sym] = today
            if newest <= by[sym]["fiscal_year"]:
                print("=  ", sym, "al día", newest); continue
            cur = inc["annualReports"][0].get("reportedCurrency", "USD")
            cost = CHECK_COST_FULL + (0 if cur == "USD" else 1)
            if used + cost > BUDGET:
                state[sym] = ""                              # pendiente: tendrá prioridad la próxima vez
                print("PENDIENTE", sym, "cuentas nuevas", newest, "(sin presupuesto hoy)"); continue
            fx = get_fx(cur, key)
            _, name, sector, region = meta[sym]
            r = compute(sym, name, sector, inc, get("BALANCE_SHEET", sym, key),
                        get("CASH_FLOW", sym, key), get("GLOBAL_QUOTE", sym, key), fx, 1, region)
            used += cost
            by[sym].clear(); by[sym].update(r)
            print("NUEVAS CUENTAS", sym, newest)
        except Exception as e:
            print("FALLO", sym, str(e)[:100])
    STATE.write_text(json.dumps(state, indent=1, sort_keys=True))
    data["updated"] = today
    save_data(data, before)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "prices"
    {"prices": prices, "filings": filings, "full": full}[mode]()
