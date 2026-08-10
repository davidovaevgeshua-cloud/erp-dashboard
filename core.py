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

ISS = "https://iss.moex.com/iss"
HEADERS = {"User-Agent": "Mozilla/5.0 (erp-dashboard)"}
TIMEOUT = 30

COL_IMOEX = "#2563eb"
COL_OFZ = "#d97706"
COL_ERP = "#059669"
COL_PE = "#7c3aed"
COL_MEAN = "#dc2626"
COL_MED = "#059669"

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
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            import time
            time.sleep(1.5 * (attempt + 1))
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


def nelson_siegel_5y(params: dict) -> float:
    """Доходность КБД на сроке 5 лет по методике Московской биржи."""
    t = 5.0
    b0, b1, b2, tau = params["B1"], params["B2"], params["B3"], params["T1"]
    x = t / tau
    exp_x = math.exp(-x)
    g = b0 + (b1 + b2) * (1 - exp_x) / x - b2 * exp_x
    # 9 гауссовых поправок
    A = [0, 0.6, 1.4, 2.4, 3.6, 5.0, 6.6, 8.4, 10.4]
    for i in range(9):
        g += params.get(f"G{i+1}", 0.0) * math.exp(-((t - A[i]) ** 2) / (0.6 ** 2))
    return g


def fetch_ofz5y(date: dt.date) -> float | None:
    js = _iss_get(f"{ISS}/history/engines/stock/zcyc.json",
                  {"date": date.isoformat(), "iss.meta": "off"})
    block = js.get("yearyields") or js.get("params") or {}
    cols, data = block.get("columns", []), block.get("data", [])
    if not data:
        return None
    rec = dict(zip(cols, data[-1]))
    if all(k in rec for k in ("B1", "B2", "B3", "T1")):
        return nelson_siegel_5y(rec)
    # запасной путь: таблица доходностей по срокам
    for row in data:
        r = dict(zip(cols, row))
        if abs(float(r.get("period", 0)) - 5) < 1e-6:
            return float(r.get("value"))
    return None


def update_from_moex(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """Догружает новые торговые дни с MOEX и пересчитывает ERP / P/E."""
    last = df["date"].max().date()
    start = (last + dt.timedelta(days=1)).isoformat()
    try:
        new = fetch_imoex(start)
    except Exception as exc:  # noqa: BLE001
        return df, f"Ошибка загрузки IMOEX: {exc}"
    new = new[new["date"] > pd.Timestamp(last)] if len(new) else new
    if new is None or len(new) == 0:
        return df, f"Новых торговых дней после {last.strftime('%d.%m.%Y')} нет"
    recs, errors = [], 0
    for _, row in new.iterrows():
        d = row["date"].date()
        try:
            y = fetch_ofz5y(d)
        except Exception:  # noqa: BLE001
            y, errors = None, errors + 1
        if y is None:
            continue
        eps = EPS_FORWARD.get(d.year, list(EPS_FORWARD.values())[-1])
        coe = eps / row["imoex"]
        recs.append({"date": row["date"], "imoex": row["imoex"], "ofz5y": y,
                     "eps_fwd": eps, "coe": coe, "erp": coe - y / 100.0,
                     "pe": row["imoex"] / eps})
    if not recs:
        return df, "Новые свечи получены, но кривая КБД недоступна"
    out = pd.concat([df, pd.DataFrame(recs)], ignore_index=True)
    out = out.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    out.to_csv(DAILY_CSV, index=False)
    rebuild_bars(out)
    msg = f"Добавлено дней: {len(recs)}, последняя дата {out['date'].max():%d.%m.%Y}"
    return out, msg


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
    fig.update_layout(
        **LAYOUT,
        title=dict(text="Форвардный ERP по дням, %", x=0.01, font=dict(size=18)),
        xaxis=dict(rangeslider=dict(visible=True, thickness=0.08),
                   rangeselector=RANGE_BUTTONS, type="date"),
        yaxis=dict(title="ERP, %", ticksuffix="%"),
        height=520, showlegend=False)
    fig.add_annotation(x=0.01, y=-0.30, xref="paper", yref="paper", showarrow=False,
                       align="left", font=dict(size=11, color=COL_MEAN),
                       text=f"— — Историческое среднее 2014–2025: {mean_hist:.2f}%")
    return fig


def fig_daily_pe(df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["pe"], mode="lines", name="P/E",
        line=dict(color=COL_PE, width=2),
        hovertemplate="%{x|%d.%m.%Y}<br>P/E: %{y:.2f}<extra></extra>"))
    fig.add_hline(y=PE_BENCH, line=dict(color=COL_MED, width=1.2, dash="dot"))
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
                       align="left", font=dict(size=11, color=COL_MED),
                       text=f"···· Средний P/E 2016–2018: {PE_BENCH}")
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
