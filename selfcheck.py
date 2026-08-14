"""Проверка целостности данных перед публикацией.

Запускается в workflow после обновления. Если находит невозможные значения —
завершается с кодом 1, коммит не выполняется и на сайте остаётся прошлая рабочая версия.
"""
from __future__ import annotations

import datetime as dt
import sys

import pandas as pd

import core


def main() -> int:
    errs: list[str] = []
    df = core.load_daily()

    bad = core.bad_rows(df)
    if len(bad):
        for i in bad[:10]:
            r = df.loc[i]
            errs.append(f"строка {r['date']:%d.%m.%Y}: ofz5y={r['ofz5y']:.4f}, "
                        f"erp={r['erp'] * 100:.2f}%, imoex={r['imoex']:.2f}, pe={r['pe']:.2f}")

    if df["date"].duplicated().any():
        errs.append("есть повторяющиеся даты")
    if not df["date"].is_monotonic_increasing:
        errs.append("даты не упорядочены")

    # разрыв доходности между соседними днями более 3 п.п. — признак сбоя источника
    jump = df["ofz5y"].diff().abs()
    for i in jump.index[jump > 3.0]:
        errs.append(f"скачок доходности {df.at[i, 'date']:%d.%m.%Y}: "
                    f"{df.at[i - 1, 'ofz5y']:.2f} → {df.at[i, 'ofz5y']:.2f}")

    stale = (dt.datetime.now(core.MSK).date() - df["date"].max().date()).days
    if stale > 5:
        errs.append(f"данные устарели: последняя дата {df['date'].max():%d.%m.%Y}")

    for path, col in ((core.ERP_Q_CSV, "erp"), (core.PE_Q_CSV, "pe")):
        b = pd.read_csv(path)
        if b[col].isna().any():
            errs.append(f"{path}: пустые значения")
        if b["label"].duplicated().any():
            errs.append(f"{path}: повторяющиеся подписи")

    last = df.iloc[-1]
    print(f"строк: {len(df)}, период {df['date'].min():%d.%m.%Y}–{df['date'].max():%d.%m.%Y}")
    print(f"последний день: IMOEX {last['imoex']:.2f}, ОФЗ 5Y {last['ofz5y']:.4f}%, "
          f"ERP {last['erp'] * 100:.2f}%, P/E {last['pe']:.2f}")
    print(f"ERP: мин {df['erp'].min() * 100:.2f}%, макс {df['erp'].max() * 100:.2f}%")

    if errs:
        print("\nПРОВЕРКА НЕ ПРОЙДЕНА:")
        for e in errs:
            print("  -", e)
        return 1
    print("\nПроверка пройдена: аномалий нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
