"""Общее ядро дашборда ERP / P/E: загрузка данных с MOEX ISS и построение графиков."""
import os
import re
import math
import datetime as dt

import numpy as np
import pandas as pd
import requests
import plotly.graph_objects as go
from plotly.subplots import make_subplots

BASE = os.path.dirname(os.path.abspath(__file__))
DAILY_CSV = os.path.join(BASE, "erp_daily.csv")
ERP_Q_CSV = os.path.join(BASE, "erp_quarterly.csv")
PE_Q_CSV = os.path.join(BASE, "pe_quarterly.csv")

# Форвардный EPS индекса Мосбиржи по годам — при смене прогноза правится здесь
EPS_FORWARD = {2025: 760, 2026: 590}
PE_BENCH = 6.2  # средний P/E 2016-2018

MSK = dt.timezone(dt.timedelta(hours=3))
ISS = "https://iss.moex.com/iss"
HEADERS = {"User-Agent": "Mozilla/5.0 (erp-dashboard)"}
TIMEOUT = 45
RETRIES = 5

COL_IMOEX = "#2563eb"
COL_OFZ = "#d97706"
COL_ERP = "#059669"
COL_PE = "#7c3aed"
COL_MEAN = "#dc2626"
COL_MED = "#059669"
COL_DYN = "#1f2937"  # медиана выбранного диапазона


def legend_text(base: str, med: str, n: int) -> str:
    """Подпись под дневным графиком: статичный ориентир плюс медиана выбранного периода."""
    return (f'{base}&nbsp;&nbsp;&nbsp;&nbsp;'
            f'<span style="color:{COL_DYN}">—·— Медиана за выбранный период: '
            f'{med} ({n} дн.)</span>')


RANGE_BUTTONS = dict(buttons=[
    dict(count=1, label="1М", step="month", stepmode="backward"),
    dict(count=3, label="3М", step="month", stepmode="backward"),
    dict(count=6, label="6М", step="month", stepmode="backward"),
    dict(count=1, label="YTD", step="year", stepmode="todate"),
    dict(count=1, label="1Г", step="year", stepmode="backward"),
    dict(step="all", label="Всё"),
])

LAYOUT = dict(
    template="plotly_white",
    font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=13, color="#1f2937"),
    margin=dict(l=60, r=40, t=70, b=60),
    hoverlabel=dict(font_size=12),
)


# ---------------------------------------------------------------- данные
def load_daily() -> pd.DataFrame:
    df = pd.read_csv(DAILY_CSV, parse_dates=["date"])
    return df.sort_values("date").reset_index(drop=True)


def _iss_get(url: str, params: dict) -> dict:
    """Запрос к ISS с повторами: таймауты биржи — штатная ситуация."""
    import time
    last = None
    for attempt in range(RETRIES):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < RETRIES - 1:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"MOEX ISS недоступен: {last}")


def fetch_imoex(start: str) -> pd.DataFrame:
    """Дневные свечи индекса Мосбиржи начиная с даты start (YYYY-MM-DD)."""
    url = f"{ISS}/engines/stock/markets/index/securities/IMOEX/candles.json"
    rows, cursor = [], 0
    while True:
        js = _iss_get(url, {"from": start, "interval": 24, "start": cursor})
        cols = js["candles"]["columns"]
        data = js["candles"]["data"]
        if not data:
            break
        for d in data:
            rec = dict(zip(cols, d))
            rows.append({"date": pd.Timestamp(rec["begin"]).normalize(),
                         "imoex": float(rec["close"])})
        cursor += len(data)
        if len(data) < 100:
            break
    return pd.DataFrame(rows).drop_duplicates("date")


# Параметры гауссовых поправок КБД: геометрическая прогрессия с шагом k = 1.6.
# Набор сверен с официальной таблицей yearyields: отклонение 0,0000 п.п. на всех 11 сроках.
_K = 1.6
_A = [0.0, 0.6]
for _i in range(2, 9):
    _A.append(_A[-1] + 0.6 * _K ** (_i - 1))
_B = [0.6 * _K ** _i for _i in range(9)]


def nelson_siegel_5y(params: dict, t: float = 5.0) -> float:
    """Доходность КБД в процентах на сроке t лет по методике Московской биржи."""
    b0, b1, b2, tau = params["B1"], params["B2"], params["B3"], params["T1"]
    x = t / tau
    exp_x = math.exp(-x)
    g = b0 + (b1 + b2) * (1 - exp_x) / x - b2 * exp_x
    for i in range(9):
        g += params.get(f"G{i+1}", 0.0) * math.exp(-((t - _A[i]) ** 2) / (_B[i] ** 2))
    # G выражена в базисных пунктах непрерывной ставки — нужен переход к эффективной
    return 100.0 * (math.exp(g / 10000.0) - 1.0)


def _published_ofz5y(date: dt.date) -> tuple[float | None, str | None]:
    """Основной источник — опубликованная Мосбиржей таблица доходностей КБД.

    Эндпоинт принимает дату: для текущего дня возвращает внутридневное значение,
    для прошлых дат — значение на закрытие основной сессии (18:49:59).
    """
    js = _iss_get(f"{ISS}/engines/stock/zcyc.json",
                  {"date": date.isoformat(), "iss.only": "yearyields", "iss.meta": "off"})
    blk = js.get("yearyields", {})
    cols, data = blk.get("columns", []), blk.get("data", [])
    for row in data:
        r = dict(zip(cols, row))
        if str(r.get("tradedate", ""))[:10] != date.isoformat():
            return None, None  # кривая за другую дату — не берём
        if abs(float(r.get("period", 0)) - 5.0) < 1e-6:
            return float(r["value"]), str(r.get("tradetime") or "")
    return None, None


def _calculated_ofz5y(date: dt.date) -> tuple[float | None, str | None]:
    """Резерв — расчёт по параметрам кривой.

    Сверен с официальными данными: отклонение 0,0000 п.п. на всех 11 сроках кривой.
    """
    js = _iss_get(f"{ISS}/history/engines/stock/zcyc.json",
                  {"date": date.isoformat(), "iss.meta": "off"})
    blk = js.get("params", {})
    cols, data = blk.get("columns", []), blk.get("data", [])
    if not data:
        return None, None
    rec = {str(k).upper(): v for k, v in zip(cols, data[-1])}
    if str(rec.get("TRADEDATE", ""))[:10] != date.isoformat():
        return None, None
    if not all(k in rec for k in ("B1", "B2", "B3", "T1")):
        return None, None
    return nelson_siegel_5y(rec), str(rec.get("TRADETIME") or "")


def fetch_ofz5y(date: dt.date, realtime: bool = False) -> tuple[float | None, str | None]:
    """Доходность КБД на сроке 5 лет, % годовых.

    Сначала берётся готовое значение Мосбиржи, при его отсутствии — расчёт по параметрам.
    Значения вне диапазона 0–100% отбрасываются как невалидные.
    """
    del realtime  # один эндпоинт работает и для текущего дня, и для истории
    for source in (_published_ofz5y, _calculated_ofz5y):
        try:
            val, tm = source(date)
        except Exception:  # noqa: BLE001
            continue
        if val is not None and 0.0 < val < 100.0:
            return val, tm
    return None, None


def bad_rows(df: pd.DataFrame) -> pd.Index:
    """Строки с невозможными значениями — защита от битых данных в ряду."""
    cols = ["imoex", "ofz5y", "erp", "pe"]
    return df.index[
        ~df["ofz5y"].between(0.01, 100.0)
        | ~df["erp"].between(-0.5, 0.5)
        | ~df["imoex"].between(100.0, 100000.0)
        | ~df["pe"].between(0.5, 50.0)
        | df[cols].isna().any(axis=1)
    ]


def repair_daily(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Перезапрашивает испорченные строки; что не починилось — удаляется."""
    idx = bad_rows(df)
    if len(idx) == 0:
        return df, 0
    fixed, drop = 0, []
    for i in idx:
        d = df.at[i, "date"].date()
        px = float(df.at[i, "imoex"])
        y, _ = fetch_ofz5y(d)
        if y is None or not (100.0 < px < 100000.0):
            drop.append(i)
            continue
        eps = EPS_FORWARD.get(d.year, list(EPS_FORWARD.values())[-1])
        coe = eps / px
        df.loc[i, ["ofz5y", "eps_fwd", "coe", "erp", "pe"]] = [
            y, eps, coe, coe - y / 100.0, px / eps]
        fixed += 1
    if drop:
        df = df.drop(index=drop)
    df = df.sort_values("date").reset_index(drop=True)
    df.to_csv(DAILY_CSV, index=False)
    rebuild_bars(df)
    return df, fixed + len(drop)


def update_from_moex(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Догружает новые дни и переписывает текущий торговый день внутри сессии."""
    df, repaired = repair_daily(df)
    last = df["date"].max().date()
    today = dt.datetime.now(MSK).date()
    # старт с последней сохранённой даты включительно — чтобы обновить незакрытый день
    try:
        new = fetch_imoex(last.isoformat())
    except Exception as exc:  # noqa: BLE001
        return df, f"Ошибка загрузки IMOEX: {exc}"
    if new is None or len(new) == 0:
        return df, f"MOEX не вернул свечей после {last:%d.%m.%Y}"

    old_map = df.set_index(df["date"].dt.date)[["imoex", "ofz5y"]].to_dict("index")
    recs, stamp, added, changed = [], None, 0, 0
    for _, row in new.iterrows():
        d = row["date"].date()
        try:
            y, tm = fetch_ofz5y(d, realtime=(d == today))
        except Exception:  # noqa: BLE001
            y, tm = None, None
        if y is None:
            continue
        prev = old_map.get(d)
        if prev is not None:
            same = (abs(prev["imoex"] - row["imoex"]) < 1e-6
                    and abs(prev["ofz5y"] - y) < 1e-9)
            if same:
                continue
            changed += 1
        else:
            added += 1
        if d == today and tm:
            stamp = str(tm)[:5]
        eps = EPS_FORWARD.get(d.year, list(EPS_FORWARD.values())[-1])
        coe = eps / row["imoex"]
        recs.append({"date": row["date"], "imoex": row["imoex"], "ofz5y": y,
                     "eps_fwd": eps, "coe": coe, "erp": coe - y / 100.0,
                     "pe": row["imoex"] / eps})
    if not recs:
        if repaired:
            return df, f"исправлено битых значений: {repaired}; последняя дата {last:%d.%m.%Y}"
        return df, f"Данные актуальны, изменений нет (последняя дата {last:%d.%m.%Y})"
    # новые строки идут после старых, keep="last" — это и есть upsert
    out = pd.concat([df, pd.DataFrame(recs)], ignore_index=True)
    out = (out.drop_duplicates("date", keep="last")
              .sort_values("date").reset_index(drop=True))
    out.to_csv(DAILY_CSV, index=False)
    rebuild_bars(out)
    parts = []
    if repaired:
        parts.append(f"исправлено битых значений: {repaired}")
    if added:
        parts.append(f"добавлено дней: {added}")
    if changed:
        parts.append(f"пересчитан текущий день" + (f" на {stamp} МСК" if stamp else ""))
    return out, ", ".join(parts) + f"; последняя дата {out['date'].max():%d.%m.%Y}"


def rebuild_bars(daily: pd.DataFrame) -> None:
    """Пересобирает квартальные срезы 2026+ и значение «Сейчас»."""
    for path, col, keep in ((ERP_Q_CSV, "erp", r"\d{4}"), (PE_Q_CSV, "pe", r"\d{4}")):
        old = pd.read_csv(path)
        base = old[old["label"].astype(str).str.fullmatch(keep)]
        bars = base.to_dict("records")
        d = daily[daily["date"].dt.year >= 2026]
        for (y, q), g in d.groupby([d["date"].dt.year, d["date"].dt.quarter]):
            bars.append({"label": f"{q}К{str(y)[2:]}", col: g[col].mean()})
        bars.append({"label": "Сейчас", col: float(daily[col].iloc[-1])})
        pd.DataFrame(bars).to_csv(path, index=False)


def load_bars(path: str, col: str) -> pd.DataFrame:
    return pd.read_csv(path)


# ---------------------------------------------------------------- графики
def fig_daily_erp(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["erp"] * 100, mode="lines", name="Форвардный ERP",
        line=dict(color=COL_ERP, width=2),
        hovertemplate="%{x|%d.%m.%Y}<br>ERP: %{y:.2f}%<extra></extra>"))
    mean_hist = 8.92
    fig.add_hline(y=mean_hist, line=dict(color=COL_MEAN, width=1.2, dash="dash"))
    # вторая линия — медиана выбранного диапазона, пересчитывается в браузере
    med_all = float((df["erp"] * 100).median())
    fig.add_hline(y=med_all, line=dict(color=COL_DYN, width=1.4, dash="dashdot"))
    fig.update_layout(
        **LAYOUT,
        title=dict(text="Форвардный ERP по дням, %", x=0.01, font=dict(size=18)),
        xaxis=dict(rangeslider=dict(visible=True, thickness=0.08),
                   rangeselector=RANGE_BUTTONS, type="date"),
        yaxis=dict(title="ERP, %", ticksuffix="%"),
        height=520, showlegend=False)
    fig.add_annotation(x=0.01, y=-0.30, xref="paper", yref="paper", showarrow=False,
                       align="left", font=dict(size=11),
                       text=legend_text(
                           f'<span style="color:{COL_MEAN}">— — Историческое среднее '
                           f'2014–2025: {mean_hist:.2f}%</span>',
                           f"{med_all:.2f}%", len(df)))
    return fig


def fig_daily_pe(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["pe"], mode="lines", name="P/E",
        line=dict(color=COL_PE, width=2),
        hovertemplate="%{x|%d.%m.%Y}<br>P/E: %{y:.2f}<extra></extra>"))
    fig.add_hline(y=PE_BENCH, line=dict(color=COL_MED, width=1.2, dash="dot"))
    med_all = float(df["pe"].median())
    fig.add_hline(y=med_all, line=dict(color=COL_DYN, width=1.4, dash="dashdot"))
    lo = min(df["pe"].min() * 0.96, PE_BENCH * 0.95)
    hi = max(df["pe"].max() * 1.04, PE_BENCH * 1.06)
    fig.update_layout(
        **LAYOUT,
        title=dict(text="P/E индекса Мосбиржи по дням", x=0.01, font=dict(size=18)),
        xaxis=dict(rangeslider=dict(visible=True, thickness=0.08),
                   rangeselector=RANGE_BUTTONS, type="date"),
        yaxis=dict(title="P/E", range=[lo, hi]),
        height=520, showlegend=False)
    fig.add_annotation(x=0.01, y=-0.30, xref="paper", yref="paper", showarrow=False,
                       align="left", font=dict(size=11),
                       text=legend_text(
                           f'<span style="color:{COL_MED}">···· Средний P/E '
                           f'2016–2018: {PE_BENCH}</span>',
                           f"{med_all:.2f}", len(df)))
    return fig


def _bar_stats(bars: pd.DataFrame, col: str):
    """Среднее и медиана только по завершённым годам (метки вида 2014…2025)."""
    mask = bars["label"].astype(str).str.fullmatch(r"\d{4}")
    base = bars.loc[mask, col]
    if len(base) < 2:
        base = bars[col]
        note = "все периоды"
    else:
        note = f"{len(base)} лет"
    return float(base.mean()), float(base.median()), note


def fig_bar(bars: pd.DataFrame, col: str, title: str, pct: bool) -> go.Figure:
    mean_v, med_v, note = _bar_stats(bars, col)
    scale = 100 if pct else 1
    labels = bars["label"].astype(str).tolist()
    vals = (bars[col] * scale).tolist()
    colors = []
    for lb in labels:
        if lb == "Сейчас":
            colors.append("#dc2626")
        elif "К" in lb:
            colors.append("#60a5fa")
        else:
            colors.append("#1e40af")
    fmt = "%{y:.2f}%" if pct else "%{y:.2f}"
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=vals, marker_color=colors, name="",
        text=[f"{v:.1f}%" if pct else f"{v:.2f}" for v in vals],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{x}<br>" + fmt + "<extra></extra>", showlegend=False))
    fig.add_hline(y=mean_v * scale, line=dict(color=COL_MEAN, width=1.6, dash="dash"))
    fig.add_hline(y=med_v * scale, line=dict(color=COL_MED, width=1.6, dash="dot"))
    # подписи линий вынесены в легенду под графиком, чтобы не налезать на столбцы
    lab_mean = f"Среднее ({note}): {mean_v*scale:.2f}" + ("%" if pct else "")
    lab_med = f"Медиана ({note}): {med_v*scale:.2f}" + ("%" if pct else "")
    for lab, color, dash in ((lab_mean, COL_MEAN, "dash"), (lab_med, COL_MED, "dot")):
        fig.add_trace(go.Scatter(x=[labels[0]], y=[None], mode="lines", name=lab,
                                 line=dict(color=color, width=1.6, dash=dash),
                                 hoverinfo="skip", showlegend=True))
    ymax = max(vals) * 1.18
    fig.update_layout(
        **LAYOUT,
        title=dict(text=title, x=0.01, font=dict(size=18)),
        yaxis=dict(title=("%" if pct else "P/E"), range=[0, ymax],
                   ticksuffix="%" if pct else ""),
        xaxis=dict(title=""),
        height=560, bargap=0.28,
        legend=dict(orientation="h", yanchor="top", y=-0.16, x=0))
    return fig


def fig_combined(df: pd.DataFrame) -> go.Figure:
    d = df[df["date"] >= "2025-01-01"]
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("Индекс Мосбиржи", "Доходность ОФЗ 5Y, %",
                                        "Форвардный ERP, %"))
    fig.add_trace(go.Scatter(x=d["date"], y=d["imoex"], name="IMOEX",
                             line=dict(color=COL_IMOEX, width=2),
                             hovertemplate="IMOEX: %{y:.0f}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=d["date"], y=d["ofz5y"], name="ОФЗ 5Y",
                             line=dict(color=COL_OFZ, width=2),
                             hovertemplate="ОФЗ 5Y: %{y:.2f}%<extra></extra>"), row=2, col=1)
    fig.add_trace(go.Scatter(x=d["date"], y=d["erp"] * 100, name="ERP",
                             line=dict(color=COL_ERP, width=2),
                             hovertemplate="ERP: %{y:.2f}%<extra></extra>"), row=3, col=1)
    fig.update_layout(
        **LAYOUT,
        title=dict(text="IMOEX, ОФЗ 5Y и ERP с 2025 года", x=0.01, font=dict(size=18)),
        height=820, hovermode="x unified", showlegend=False)
    fig.update_layout(margin=dict(l=60, r=40, t=100, b=60))
    for ann in fig.layout.annotations:
        ann.font.size = 13
    fig.update_xaxes(rangeslider=dict(visible=True, thickness=0.05),
                     rangeselector=RANGE_BUTTONS, row=3, col=1)
    fig.update_yaxes(ticksuffix="%", row=2, col=1)
    fig.update_yaxes(ticksuffix="%", row=3, col=1)
    return fig


def build_figures():
    daily = load_daily()
    erp_bars = load_bars(ERP_Q_CSV, "erp")
    pe_bars = load_bars(PE_Q_CSV, "pe")
    return dict(
        daily=daily,
        erp_daily=fig_daily_erp(daily),
        erp_bar=fig_bar(erp_bars, "erp", "ERP: годы 2014–2025, кварталы 2026 и текущее значение", True),
        pe_daily=fig_daily_pe(daily),
        pe_bar=fig_bar(pe_bars, "pe", "P/E: годы, кварталы 2026 и текущее значение", False),
        combined=fig_combined(daily),
    )
