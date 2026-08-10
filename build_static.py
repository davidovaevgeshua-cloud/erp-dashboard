"""Пересобирает docs/index.html со свежими данными MOEX (фолбэк — сохранённые CSV)."""
import os
import sys
import datetime as dt

import plotly.io as pio

import core

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
os.makedirs(DOCS, exist_ok=True)

status = "данные из репозитория"
if "--no-update" not in sys.argv:
    try:
        df, msg = core.update_from_moex(core.load_daily())
        status = msg
    except Exception as exc:  # noqa: BLE001
        status = f"обновление с MOEX не выполнено ({exc}), показаны сохранённые данные"
print("STATUS:", status)

figs = core.build_figures()
daily = figs["daily"]
last = daily.iloc[-1]
last_date = daily["date"].max()

MSK = dt.timezone(dt.timedelta(hours=3))
built_at = dt.datetime.now(dt.timezone.utc).astimezone(MSK)
SCHEDULE = "по будням в 18:30 МСК"

CFG = {"displaylogo": False, "responsive": True,
       "modeBarButtonsToRemove": ["lasso2d", "select2d"]}


def div(fig, first=False):
    return pio.to_html(fig, include_plotlyjs="cdn" if first else False,
                       full_html=False, config=CFG)


cards = [
    ("ERP на последнюю дату", f"{last['erp']*100:.2f}%"),
    ("P/E индекса", f"{last['pe']:.2f}"),
    ("IMOEX", f"{last['imoex']:,.0f}".replace(",", " ")),
    ("ОФЗ 5Y", f"{last['ofz5y']:.2f}%"),
]
cards_html = "".join(
    f'<div class="card"><div class="card-label">{k}</div>'
    f'<div class="card-value">{v}</div></div>' for k, v in cards)

html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ERP и P/E индекса Мосбиржи</title>
<style>
  :root {{ --ink:#111827; --muted:#6b7280; --line:#e5e7eb; --bg:#f8fafc; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:Inter,"Segoe UI",Arial,sans-serif; }}
  header {{ background:#0f172a; color:#fff; padding:32px 24px; }}
  .wrap {{ max-width:1180px; margin:0 auto; padding:0 24px; }}
  header h1 {{ margin:0 0 6px; font-size:26px; font-weight:600; }}
  header p {{ margin:0; color:#94a3b8; font-size:14px; }}
  header p.sched {{ margin-top:6px; font-size:13px; color:#a3b2c4; }}
  .cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
            gap:14px; margin:24px 0 8px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
  .card-label {{ font-size:12px; color:var(--muted); text-transform:uppercase;
                 letter-spacing:.04em; }}
  .card-value {{ font-size:26px; font-weight:600; margin-top:6px; }}
  section {{ background:#fff; border:1px solid var(--line); border-radius:14px;
             padding:20px 20px 28px; margin:22px 0; }}
  h2 {{ font-size:20px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 16px; }}
  .note {{ color:var(--muted); font-size:12px; margin-top:10px; line-height:1.5; }}
  footer {{ color:var(--muted); font-size:12px; padding:24px; text-align:center; }}
</style>
</head>
<body>
<header><div class="wrap">
  <h1>ERP и P/E индекса Мосбиржи</h1>
  <p>Данные на {last_date:%d.%m.%Y} · страница пересобрана {built_at:%d.%m.%Y %H:%M} МСК</p>
  <p class="sched">Автообновление {SCHEDULE} · результат последнего запуска: {status}</p>
</div></header>
<div class="wrap">
  <div class="cards">{cards_html}</div>

  <section>
    <h2>Раздел 1. Форвардный ERP</h2>
    <p class="sub">ERP = EPS<sub>fwd</sub> / IMOEX − доходность ОФЗ 5Y. Таймфрейм сужается
       кнопками, ползунком под графиком или выделением мышью.</p>
    {div(figs['erp_daily'], first=True)}
    {div(figs['erp_bar'])}
    <p class="note">Годы 2014–2025 — исторические значения; кварталы 2026 рассчитаны как
       среднее дневных значений; «Сейчас» — ERP на последнюю доступную дату.
       Среднее и медиана считаются только по завершённым годам 2014–2025.</p>
  </section>

  <section>
    <h2>Раздел 2. P/E индекса</h2>
    <p class="sub">P/E = IMOEX / EPS<sub>fwd</sub> — рассчитывается из тех же данных и
       обновляется вместе с ERP.</p>
    {div(figs['pe_daily'])}
    {div(figs['pe_bar'])}
    <p class="note">Зелёная линия на дневном графике — средний P/E 2016–2018 ({core.PE_BENCH}).</p>
  </section>

  <section>
    <h2>Раздел 3. Совмещённая динамика</h2>
    <p class="sub">Три синхронизированные панели с 2025 года: индекс, безрисковая ставка
       и премия за риск. Подсказка показывает все три значения на одну дату.</p>
    {div(figs['combined'])}
  </section>
</div>
<footer>Источник данных: MOEX ISS API (свечи IMOEX и кривая бескупонной доходности).
  Автообновление {SCHEDULE}; новые точки появляются только по торговым дням.
  Сборка {built_at:%d.%m.%Y %H:%M} МСК.</footer>
</body>
</html>
"""

out = os.path.join(DOCS, "index.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)
print("written:", out, os.path.getsize(out) // 1024, "KB")
