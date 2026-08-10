"""Dash-версия дашборда ERP / P/E с автообновлением данных с MOEX ISS."""
import os

from dash import Dash, dcc, html, Input, Output, State, no_update

import core

app = Dash(__name__, title="ERP и P/E индекса Мосбиржи")
server = app.server  # для gunicorn

CFG = {"displaylogo": False, "responsive": True}
CARD = {"background": "#fff", "border": "1px solid #e5e7eb", "borderRadius": "12px",
        "padding": "16px 18px", "flex": "1 1 200px"}
SECTION = {"background": "#fff", "border": "1px solid #e5e7eb", "borderRadius": "14px",
           "padding": "20px", "margin": "22px 0"}


def cards(daily):
    last = daily.iloc[-1]
    items = [("ERP на последнюю дату", f"{last['erp']*100:.2f}%"),
             ("P/E индекса", f"{last['pe']:.2f}"),
             ("IMOEX", f"{last['imoex']:,.0f}".replace(",", " ")),
             ("ОФЗ 5Y", f"{last['ofz5y']:.2f}%")]
    return [html.Div([
        html.Div(k, style={"fontSize": "12px", "color": "#6b7280",
                           "textTransform": "uppercase", "letterSpacing": ".04em"}),
        html.Div(v, style={"fontSize": "26px", "fontWeight": 600, "marginTop": "6px"}),
    ], style=CARD) for k, v in items]


app.layout = html.Div([
    html.Header([
        html.Div([
            html.H1("ERP и P/E индекса Мосбиржи",
                    style={"margin": "0 0 6px", "fontSize": "26px", "fontWeight": 600}),
            html.P("Данные обновляются с MOEX ISS API",
                   style={"margin": 0, "color": "#94a3b8", "fontSize": "14px"}),
        ], style={"maxWidth": "1180px", "margin": "0 auto", "padding": "0 24px"}),
    ], style={"background": "#0f172a", "color": "#fff", "padding": "32px 0"}),

    html.Div([
        html.Div([
            html.Button("Обновить с MOEX", id="btn-refresh", n_clicks=0,
                        style={"padding": "10px 18px", "borderRadius": "8px",
                               "border": "1px solid #1e40af", "background": "#1e40af",
                               "color": "#fff", "cursor": "pointer", "fontSize": "14px"}),
            html.Span(id="status", style={"marginLeft": "14px", "color": "#6b7280",
                                          "fontSize": "13px"}),
        ], style={"margin": "22px 0 4px"}),

        html.Div(id="cards", style={"display": "flex", "gap": "14px",
                                    "flexWrap": "wrap", "margin": "16px 0"}),

        html.Section([
            html.H2("Раздел 1. Форвардный ERP", style={"fontSize": "20px", "margin": "0 0 4px"}),
            html.P("ERP = EPS_fwd / IMOEX − доходность ОФЗ 5Y.",
                   style={"color": "#6b7280", "fontSize": "13px", "margin": "0 0 16px"}),
            dcc.Graph(id="g-erp-daily", config=CFG),
            dcc.Graph(id="g-erp-bar", config=CFG),
            html.P("Среднее и медиана считаются только по завершённым годам 2014–2025; "
                   "кварталы 2026 — среднее дневных значений.",
                   style={"color": "#6b7280", "fontSize": "12px"}),
        ], style=SECTION),

        html.Section([
            html.H2("Раздел 2. P/E индекса", style={"fontSize": "20px", "margin": "0 0 4px"}),
            html.P("P/E = IMOEX / EPS_fwd, те же данные и то же автообновление.",
                   style={"color": "#6b7280", "fontSize": "13px", "margin": "0 0 16px"}),
            dcc.Graph(id="g-pe-daily", config=CFG),
            dcc.Graph(id="g-pe-bar", config=CFG),
        ], style=SECTION),

        html.Section([
            html.H2("Раздел 3. Совмещённая динамика",
                    style={"fontSize": "20px", "margin": "0 0 4px"}),
            html.P("IMOEX, ОФЗ 5Y и ERP с 2025 года на общей оси времени.",
                   style={"color": "#6b7280", "fontSize": "13px", "margin": "0 0 16px"}),
            dcc.Graph(id="g-combined", config=CFG),
        ], style=SECTION),
    ], style={"maxWidth": "1180px", "margin": "0 auto", "padding": "0 24px"}),

    dcc.Interval(id="tick", interval=15 * 60 * 1000, n_intervals=0),
], style={"background": "#f8fafc", "minHeight": "100vh", "margin": 0,
          "fontFamily": "Inter, Segoe UI, Arial, sans-serif", "color": "#111827"})


@app.callback(
    Output("g-erp-daily", "figure"), Output("g-erp-bar", "figure"),
    Output("g-pe-daily", "figure"), Output("g-pe-bar", "figure"),
    Output("g-combined", "figure"), Output("cards", "children"),
    Output("status", "children"),
    Input("btn-refresh", "n_clicks"), Input("tick", "n_intervals"),
)
def refresh(_clicks, _ticks):
    try:
        _, msg = core.update_from_moex(core.load_daily())
    except Exception as exc:  # noqa: BLE001
        msg = f"MOEX недоступен ({exc}) — показаны сохранённые данные"
    f = core.build_figures()
    return (f["erp_daily"], f["erp_bar"], f["pe_daily"], f["pe_bar"],
            f["combined"], cards(f["daily"]), msg)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8050)), debug=False)
