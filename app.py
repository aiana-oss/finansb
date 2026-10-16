#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Budget & Finance Manager — версия на Streamlit.

Установка:   pip install streamlit matplotlib
Запуск:      streamlit run budget_streamlit.py

Данные хранятся в файле budget.db рядом со скриптом (SQLite). Файл совместим
с desktop-версией (budget_manager.py) — схема базы одинаковая.
matplotlib необязателен: без него используются встроенные графики Streamlit.
"""

# =====================================================================
#  ЛОГИКА (без GUI): база данных, парсер, расчёт дат
# =====================================================================
import calendar
import os
import re
import sqlite3
from datetime import date, datetime, timedelta

APP_TITLE = "Budget & Finance Manager"
CURRENCY = "сом"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "budget.db")

MONTHS_RU = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
             "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# (название, тип, ключевые слова для умного ввода)
DEFAULT_CATEGORIES = [
    ("Еда", "expense", "еда,еду,еды,продукт,обед,ужин,завтрак,кафе,ресторан,пицц,магазин,хлеб,молок,шаурм,бургер,фастфуд,кофе,столов"),
    ("Транспорт", "expense", "такси,бензин,заправк,проезд,автобус,маршрутк,метро,транспорт,парковк"),
    ("Развлечения", "expense", "кино,игр,развлеч,клуб,концерт,подписк,netflix"),
    ("Жильё", "expense", "аренд,квартир,коммунал,электричеств,свет,квартплат,ипотек"),
    ("Связь", "expense", "телефон,интернет,связь,тариф,мобильн,симк"),
    ("Здоровье", "expense", "аптек,лекарств,врач,больниц,здоров,стоматолог,анализ"),
    ("Одежда", "expense", "одежд,обув,куртк,футболк,джинс,кроссовк"),
    ("Образование", "expense", "курс,учеб,книг,универс,образован,репетитор"),
    ("Прочее", "expense", ""),
    ("Зарплата", "income", "зарплат,оклад,аванс,получк"),
    ("Подработка", "income", "подработ,фриланс,заказ"),
    ("Стипендия", "income", "стипенд"),
    ("Премия", "income", "преми,бонус"),
    ("Подарки", "income", "подарил,подарок"),
    ("Прочее", "income", ""),
]


class Database:
    def __init__(self, path=DB_PATH):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.create_tables()
        self.seed()

    # ---- базовые операции ----
    def q(self, sql, args=()):
        return self.conn.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        cur = self.conn.execute(sql, args)
        self.conn.commit()
        return cur.lastrowid

    def close(self):
        self.conn.close()

    # ---- схема ----
    def create_tables(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            initial_balance REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS categories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            keywords TEXT NOT NULL DEFAULT '',
            UNIQUE(name, type)
        );
        CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('income','expense')),
            amount REAL NOT NULL,
            category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            note TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS recurring(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            amount REAL NOT NULL,
            account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            day INTEGER NOT NULL,
            last_paid_month TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS credit_cards(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            credit_limit REAL NOT NULL DEFAULT 0,
            debt REAL NOT NULL DEFAULT 0,
            min_payment REAL NOT NULL DEFAULT 0,
            pay_day INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS debts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('i_owe','owed_me')),
            date TEXT NOT NULL,
            due_date TEXT,
            status TEXT NOT NULL DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS budgets(
            category_id INTEGER PRIMARY KEY REFERENCES categories(id) ON DELETE CASCADE,
            limit_amount REAL NOT NULL
        );
        """)
        self.conn.commit()

    def seed(self):
        if not self.q("SELECT 1 FROM accounts LIMIT 1"):
            for name in ("Наличные", "Карта 1", "Карта 2"):
                self.x("INSERT INTO accounts(name, initial_balance) VALUES (?, 0)", (name,))
        if not self.q("SELECT 1 FROM categories LIMIT 1"):
            for name, kind, kw in DEFAULT_CATEGORIES:
                self.x("INSERT INTO categories(name, type, keywords) VALUES (?,?,?)", (name, kind, kw))

    # ---- запросы ----
    def accounts(self):
        return self.q("SELECT * FROM accounts ORDER BY id")

    def accounts_with_balance(self):
        return self.q("""
            SELECT a.id, a.name, a.initial_balance,
                   a.initial_balance + COALESCE(SUM(CASE t.type WHEN 'income' THEN t.amount
                                                    ELSE -t.amount END), 0) AS balance
            FROM accounts a LEFT JOIN transactions t ON t.account_id = a.id
            GROUP BY a.id ORDER BY a.id""")

    def categories(self, kind=None):
        if kind:
            return self.q("SELECT * FROM categories WHERE type=? ORDER BY name", (kind,))
        return self.q("SELECT * FROM categories ORDER BY type, name")

    def ensure_category(self, name, kind, keywords=""):
        rows = self.q("SELECT id FROM categories WHERE name=? AND type=?", (name, kind))
        if rows:
            return rows[0]["id"]
        return self.x("INSERT INTO categories(name, type, keywords) VALUES (?,?,?)", (name, kind, keywords))

    def add_transaction(self, day_iso, kind, amount, category_id, account_id, note=""):
        return self.x("INSERT INTO transactions(date,type,amount,category_id,account_id,note) "
                      "VALUES (?,?,?,?,?,?)", (day_iso, kind, amount, category_id, account_id, note))

    def budget_limits(self):
        return {r["category_id"]: r["limit_amount"] for r in self.q("SELECT * FROM budgets")}

    def spent(self, category_id, month):
        r = self.q("SELECT COALESCE(SUM(amount),0) s FROM transactions "
                   "WHERE type='expense' AND category_id=? AND substr(date,1,7)=?", (category_id, month))
        return r[0]["s"]

    def month_totals(self, month):
        res = {"income": 0.0, "expense": 0.0}
        for r in self.q("SELECT type, SUM(amount) s FROM transactions WHERE substr(date,1,7)=? GROUP BY type", (month,)):
            res[r["type"]] = r["s"] or 0.0
        return res["income"], res["expense"]

    def expense_by_category(self, month):
        return self.q("""SELECT COALESCE(c.name, 'Без категории') name, SUM(t.amount) total
                         FROM transactions t LEFT JOIN categories c ON c.id = t.category_id
                         WHERE t.type='expense' AND substr(t.date,1,7)=?
                         GROUP BY name ORDER BY total DESC""", (month,))


# ---------------------------------------------------------------------
#  Форматирование и даты
# ---------------------------------------------------------------------
def fmt(x):
    s = f"{x:,.2f}".replace(",", " ")
    if s.endswith(".00"):
        s = s[:-3]
    return f"{s} {CURRENCY}"


def num_str(v):
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}"


def fmt_iso(iso):
    if not iso:
        return "—"
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def parse_money(s, allow_zero=True, allow_negative=False):
    s = str(s).replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise ValueError("Введите сумму числом, например 1500 или 1500.50")
    if v < 0 and not allow_negative:
        raise ValueError("Сумма не может быть отрицательной")
    if v == 0 and not allow_zero:
        raise ValueError("Сумма должна быть больше нуля")
    return v


def parse_day(s):
    try:
        d = int(str(s).strip())
    except ValueError:
        raise ValueError("День месяца должен быть числом от 1 до 31")
    if not 1 <= d <= 31:
        raise ValueError("День месяца должен быть числом от 1 до 31")
    return d


def parse_date_str(s, required=True):
    s = str(s).strip()
    if not s:
        if required:
            raise ValueError("Укажите дату в формате ДД.ММ.ГГГГ")
        return None
    for f in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            pass
    raise ValueError("Дата должна быть в формате ДД.ММ.ГГГГ")


def month_key(d):
    return d.strftime("%Y-%m")


def month_title(d):
    return f"{MONTHS_RU[d.month - 1]} {d.year}"


def clamp_day(year, month, day):
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def shift_month(year, month, delta):
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def next_date_from_day(day, today=None):
    """Ближайшая дата с указанным днём месяца (сегодня включительно)."""
    today = today or date.today()
    cur = clamp_day(today.year, today.month, day)
    if cur >= today:
        return cur
    y, m = shift_month(today.year, today.month, 1)
    return clamp_day(y, m, day)


def next_recurring_date(day, paid_month, today=None):
    """Дата следующего регулярного платежа: если месяц уже оплачен — со следующего месяца,
    иначе — дата в текущем месяце (может быть в прошлом = просрочка)."""
    today = today or date.today()
    if paid_month == month_key(today):
        y, m = shift_month(today.year, today.month, 1)
        return clamp_day(y, m, day)
    return clamp_day(today.year, today.month, day)


def last_months(n, end=None):
    """Список 'YYYY-MM' от нового к старому."""
    end = end or date.today()
    return [f"{y:04d}-{m:02d}" for y, m in (shift_month(end.year, end.month, -i) for i in range(n))]


def months_before(month, n):
    """n месяцев, заканчивая указанным (по возрастанию)."""
    y, m = int(month[:4]), int(month[5:7])
    return [f"{yy:04d}-{mm:02d}" for yy, mm in (shift_month(y, m, -i) for i in range(n - 1, -1, -1))]


# ---------------------------------------------------------------------
#  «Умный» ввод: разбор фразы на естественном языке
# ---------------------------------------------------------------------
def norm(s):
    return s.lower().replace("ё", "е")


EXPENSE_VERBS = ("потратил", "потратила", "купил", "купила", "заплатил", "заплатила",
                 "оплатил", "оплатила", "расход", "списали", "списал", "отдал", "отдала")
INCOME_WORDS = ("зарплат", "получил", "получила", "получк", "пришл", "доход", "преми", "аванс",
                "подарили", "заработал", "заработала", "кэшб", "кешб", "стипенд", "подработ",
                "поступил", "продал", "продала")

NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,](\d{1,2}))?(?!\d)"
                    r"\s*(тыс\w*|к(?!\w)|k(?!\w))?")
CUR_RE = re.compile(r"\s*(сом\w*|руб\w*|р(?!\w)|₽|kgs|usd|eur|\$)")


def has_word(text, words):
    return any(re.search(r"(?<!\w)" + re.escape(w), text) for w in words)


def account_pattern(name):
    """Шаблон поиска счёта в тексте с учётом падежей: «Карта 1» найдёт «карты 1», «с карту 1»."""
    parts = []
    for tok in re.findall(r"\w+", norm(name)):
        if tok.isdigit():
            parts.append(tok + r"(?!\d)")
        else:
            n = len(tok)
            stem = tok if n <= 3 else (tok[:-1] if n <= 4 else tok[:-2])
            parts.append(re.escape(stem) + r"\w*")
    return r"(?<!\w)" + r"[\s\-]*".join(parts)


def parse_entry(text, accounts, categories, today=None):
    """Возвращает dict(kind, amount, account, category, date, note, guessed_account)
    или бросает ValueError с понятным сообщением."""
    today = today or date.today()
    original = text.strip()
    low = norm(original)

    if not accounts:
        raise ValueError("Сначала создайте хотя бы один счёт (вкладка «Счета»).")

    # дата
    entry_date = today
    if re.search(r"(?<!\w)позавчера", low):
        entry_date = today - timedelta(days=2)
    elif re.search(r"(?<!\w)вчера", low):
        entry_date = today - timedelta(days=1)

    # счёт
    best = None
    for acc in accounts:
        m = re.search(account_pattern(acc["name"]), low)
        if m and (best is None or len(m.group()) > len(best[1].group())):
            best = (acc, m)
    if best:
        account = best[0]
        rest = low[:best[1].start()] + " " + low[best[1].end():]
        guessed = False
    else:
        account = accounts[0]
        rest = low
        guessed = True

    # сумма
    cands = []
    for m in NUM_RE.finditer(rest):
        val = float(re.sub(r"\s", "", m.group(1)) + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3):
            val *= 1000
        cands.append((val, bool(CUR_RE.match(rest, m.end()))))
    if not cands:
        raise ValueError("Не нашёл сумму. Напишите, например: «потратил 300 сом на еду с карты 1».")
    with_cur = [c for c in cands if c[1]]
    amount = (with_cur[0] if with_cur else max(cands))[0]
    if amount <= 0:
        raise ValueError("Сумма должна быть больше нуля.")

    # тип операции
    if has_word(rest, EXPENSE_VERBS):
        kind = "expense"
    elif has_word(rest, INCOME_WORDS):
        kind = "income"
    else:
        kind = "expense"

    # категория
    best_cat, best_len = None, 0
    for c in categories:
        if c["type"] != kind:
            continue
        kws = [norm(k.strip()) for k in (c["keywords"] or "").split(",") if k.strip()]
        nm = norm(c["name"])
        kws.append(nm if len(nm) <= 4 else nm[:-2])
        for kw in kws:
            if kw and len(kw) > best_len and re.search(r"(?<!\w)" + re.escape(kw), rest):
                best_cat, best_len = c, len(kw)
    if best_cat is None:
        for c in categories:
            if c["type"] == kind and c["name"] == "Прочее":
                best_cat = c
                break

    return {"kind": kind, "amount": amount, "account": account, "category": best_cat,
            "date": entry_date.isoformat(), "note": original, "guessed_account": guessed}



# =====================================================================
#  ИНТЕРФЕЙС (Streamlit)
# =====================================================================
import pandas as pd
import streamlit as st

try:
    from matplotlib.figure import Figure
    HAS_MPL = True
except Exception:  # matplotlib не установлен — используем встроенные графики Streamlit
    HAS_MPL = False

DIRS = {"Я должен": "i_owe", "Мне должны": "owed_me"}


# ---------------------------------------------------------------------
#  Вспомогательные функции интерфейса
# ---------------------------------------------------------------------
def flash(msg, kind="success"):
    """Показать сообщение после перезапуска страницы (kind: success/info/warning/error)."""
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg)


def show_table(rows, columns):
    if not rows:
        st.info("Пока пусто.")
        return
    st.dataframe(pd.DataFrame(rows, columns=columns), hide_index=True)


def pick(key, label, rows, label_fn):
    """Выпадающий список для выбора записи; возвращает выбранную строку."""
    ids = [r["id"] for r in rows]
    by_id = {r["id"]: r for r in rows}
    if st.session_state.get(key) not in ids:
        st.session_state.pop(key, None)
    sel = st.selectbox(label, ids, format_func=lambda i: label_fn(by_id[i]), key=key)
    return by_id[sel]


def confirm_delete(key, what, action, done_msg):
    with st.form(f"del_{key}"):
        ok = st.checkbox(f"Подтверждаю удаление: {what}")
        if st.form_submit_button("🗑 Удалить"):
            if ok:
                action()
                flash(done_msg)
                st.rerun()
            else:
                st.warning("Поставьте галочку подтверждения.")


def account_choices(db):
    return {r["name"]: r["id"] for r in db.accounts()}


def status_icon(tag):
    return {"overdue": "🔴", "soon": "🟠", "ok": "🟢", "done": "⚪"}.get(tag, "⚫")


# ---------------------------------------------------------------------
#  1. Умный ввод
# ---------------------------------------------------------------------
def tab_input(db):
    st.subheader("Умный ввод")
    st.caption("Напишите операцию обычной фразой — сумма, категория и счёт определятся автоматически. "
               "Примеры: «зарплата 20000 сом на наличные» · «такси 150 карта 2» · "
               "«купил продукты за 1 200 с карты 1» · «премия 5к на карту 1» · «вчера кино 400 наличные»")
    n = st.session_state.get("smart_n", 0)
    with st.form("smart_form"):
        text = st.text_input("Фраза", key=f"smart_text_{n}",
                             placeholder="Например: потратил 300 сом на еду с карты 1")
        sent = st.form_submit_button("Добавить")
    if sent and text.strip():
        try:
            r = parse_entry(text, db.accounts(), db.categories())
        except ValueError as e:
            st.error(f"✖ {e}")
        else:
            cat = r["category"]
            db.add_transaction(r["date"], r["kind"], r["amount"], cat["id"] if cat else None,
                               r["account"]["id"], r["note"])
            kind_ru = "Доход" if r["kind"] == "income" else "Расход"
            lines = [f"✔ {kind_ru}: {fmt(r['amount'])} · {cat['name'] if cat else 'Без категории'} · "
                     f"{r['account']['name']} · {fmt_iso(r['date'])}"]
            level = "success"
            if r["guessed_account"]:
                lines.append(f"Счёт не указан — использован «{r['account']['name']}». "
                             "Можно удалить запись и написать счёт во фразе.")
                level = "warning"
            if r["kind"] == "expense" and cat:
                limit = db.budget_limits().get(cat["id"])
                if limit:
                    spent = db.spent(cat["id"], r["date"][:7])
                    if spent > limit:
                        lines.append(f"⚠ Лимит категории «{cat['name']}» превышен на {fmt(spent - limit)}")
                        level = "error"
                    elif spent >= 0.8 * limit:
                        lines.append(f"⚠ По категории «{cat['name']}» осталось {fmt(limit - spent)}")
                        level = "warning" if level != "error" else level
            flash("\n\n".join(lines), level)
            st.session_state["smart_n"] = n + 1
            st.rerun()

    rows = db.q("""SELECT t.id, t.date, t.type, t.amount, c.name cat, a.name acc, t.note
                   FROM transactions t JOIN accounts a ON a.id = t.account_id
                   LEFT JOIN categories c ON c.id = t.category_id
                   ORDER BY t.date DESC, t.id DESC LIMIT 200""")
    st.markdown("**Последние операции** (до 200)")
    show_table([[fmt_iso(t["date"]), "Доход" if t["type"] == "income" else "Расход",
                 ("+ " if t["type"] == "income" else "− ") + fmt(t["amount"]),
                 t["cat"] or "—", t["acc"], t["note"]] for t in rows],
               ["Дата", "Тип", "Сумма", "Категория", "Счёт", "Описание"])
    if rows:
        with st.expander("🗑 Удалить операцию"):
            t = pick("tx_sel", "Операция", rows,
                     lambda r: f"{fmt_iso(r['date'])} · {'+' if r['type'] == 'income' else '−'}{fmt(r['amount'])}"
                               f" · {r['cat'] or '—'} · {r['acc']} · {r['note']}")
            confirm_delete(f"tx_{t['id']}", "операцию",
                           lambda: db.x("DELETE FROM transactions WHERE id=?", (t["id"],)), "Операция удалена.")


# ---------------------------------------------------------------------
#  3. Счета
# ---------------------------------------------------------------------
def tab_accounts(db):
    st.subheader("Счета")
    st.caption("Баланс = начальный остаток + доходы − расходы по счёту")
    rows = db.accounts_with_balance()
    st.metric("Общий баланс", fmt(sum(r["balance"] for r in rows)))
    show_table([[r["name"], fmt(r["balance"])] for r in rows], ["Счёт", "Баланс"])

    with st.expander("➕ Добавить счёт"):
        with st.form("acc_add", clear_on_submit=True):
            name = st.text_input("Название")
            bal = st.number_input("Начальный баланс", value=0.0, step=100.0, format="%.2f")
            if st.form_submit_button("Добавить"):
                if not name.strip():
                    st.error("Укажите название счёта")
                else:
                    try:
                        db.x("INSERT INTO accounts(name, initial_balance) VALUES (?,?)", (name.strip(), bal))
                    except sqlite3.IntegrityError:
                        st.error("Счёт с таким названием уже существует")
                    else:
                        flash(f"Счёт «{name.strip()}» добавлен.")
                        st.rerun()
    if not rows:
        return
    acc = pick("acc_sel", "Выберите счёт для изменения", rows, lambda r: f"{r['name']} — {fmt(r['balance'])}")
    aid = acc["id"]
    with st.expander("✏️ Переименовать"):
        with st.form(f"acc_ren_{aid}"):
            new = st.text_input("Новое название", value=acc["name"])
            if st.form_submit_button("Сохранить"):
                if not new.strip():
                    st.error("Название не может быть пустым")
                else:
                    try:
                        db.x("UPDATE accounts SET name=? WHERE id=?", (new.strip(), aid))
                    except sqlite3.IntegrityError:
                        st.error("Счёт с таким названием уже существует")
                    else:
                        flash("Счёт переименован.")
                        st.rerun()
    with st.expander("⚖️ Скорректировать остаток"):
        with st.form(f"acc_adj_{aid}"):
            target = st.number_input("Фактический баланс сейчас", value=float(acc["balance"]),
                                     step=100.0, format="%.2f")
            if st.form_submit_button("Сохранить"):
                db.x("UPDATE accounts SET initial_balance = initial_balance + ? WHERE id=?",
                     (target - acc["balance"], aid))
                flash("Остаток скорректирован.")
                st.rerun()
    with st.expander("🗑 Удалить счёт"):
        st.warning("Будут удалены все операции и регулярные платежи этого счёта. Действие нельзя отменить.")
        confirm_delete(f"acc_{aid}", f"счёт «{acc['name']}»",
                       lambda: db.x("DELETE FROM accounts WHERE id=?", (aid,)), "Счёт удалён.")


# ---------------------------------------------------------------------
#  2. Регулярные платежи
# ---------------------------------------------------------------------
def tab_recurring(db):
    st.subheader("Регулярные платежи")
    st.caption("Кредиты, тарифы, подписки. При отметке «Оплачен» сумма списывается со счёта как расход.")
    today = date.today()
    rows = db.q("SELECT r.*, a.name AS account FROM recurring r "
                "JOIN accounts a ON a.id = r.account_id ORDER BY r.day, r.name")
    table = []
    for r in rows:
        nd = next_recurring_date(r["day"], r["last_paid_month"], today)
        if r["last_paid_month"] == month_key(today):
            status, tag = "Оплачен в этом месяце", "done"
        else:
            delta = (nd - today).days
            if delta < 0:
                status, tag = f"Просрочен на {-delta} дн.", "overdue"
            elif delta == 0:
                status, tag = "Платить сегодня!", "overdue"
            elif delta <= 3:
                status, tag = f"Через {delta} дн.", "soon"
            else:
                status, tag = f"Через {delta} дн.", "ok"
        table.append([r["name"], fmt(r["amount"]), r["account"], r["day"],
                      nd.strftime("%d.%m.%Y"), f"{status_icon(tag)} {status}"])
    show_table(table, ["Название", "Сумма", "Счёт", "День", "Следующий платёж", "Статус"])

    accs = account_choices(db)
    with st.expander("➕ Добавить платёж"):
        if not accs:
            st.warning("Сначала создайте счёт")
        else:
            with st.form("rec_add", clear_on_submit=True):
                name = st.text_input("Название")
                amount = st.number_input("Сумма", min_value=0.0, value=0.0, step=100.0, format="%.2f")
                acc = st.selectbox("Счёт списания", list(accs))
                day = st.number_input("День месяца (1–31)", min_value=1, max_value=31, value=20, step=1)
                if st.form_submit_button("Добавить"):
                    if not name.strip():
                        st.error("Укажите название платежа")
                    elif amount <= 0:
                        st.error("Сумма должна быть больше нуля")
                    else:
                        db.x("INSERT INTO recurring(name, amount, account_id, day) VALUES (?,?,?,?)",
                             (name.strip(), amount, accs[acc], int(day)))
                        flash("Платёж добавлен.")
                        st.rerun()
    if not rows:
        return
    r = pick("rec_sel", "Выберите платёж", rows, lambda x: f"{x['name']} — {fmt(x['amount'])} (день {x['day']})")
    rid = r["id"]
    if st.button("✔ Отметить оплаченным (спишет сумму со счёта)", key=f"rec_pay_{rid}"):
        if r["last_paid_month"] == month_key(today):
            st.info("Этот платёж уже отмечен оплаченным в текущем месяце.")
        else:
            cat = db.ensure_category("Регулярные платежи", "expense")
            db.add_transaction(today.isoformat(), "expense", r["amount"], cat, r["account_id"],
                               f"Регулярный платёж: {r['name']}")
            db.x("UPDATE recurring SET last_paid_month=? WHERE id=?", (month_key(today), rid))
            flash(f"«{r['name']}» оплачен: списано {fmt(r['amount'])} со счёта «{r['account']}».")
            st.rerun()
    with st.expander("✏️ Изменить"):
        with st.form(f"rec_edit_{rid}"):
            name = st.text_input("Название", value=r["name"])
            amount = st.number_input("Сумма", min_value=0.0, value=float(r["amount"]), step=100.0, format="%.2f")
            names = list(accs)
            acc = st.selectbox("Счёт списания", names,
                               index=names.index(r["account"]) if r["account"] in names else 0)
            day = st.number_input("День месяца (1–31)", min_value=1, max_value=31, value=int(r["day"]), step=1)
            if st.form_submit_button("Сохранить"):
                if not name.strip():
                    st.error("Укажите название платежа")
                elif amount <= 0:
                    st.error("Сумма должна быть больше нуля")
                else:
                    db.x("UPDATE recurring SET name=?, amount=?, account_id=?, day=? WHERE id=?",
                         (name.strip(), amount, accs[acc], int(day), rid))
                    flash("Платёж обновлён.")
                    st.rerun()
    with st.expander("🗑 Удалить"):
        confirm_delete(f"rec_{rid}", f"платёж «{r['name']}»",
                       lambda: db.x("DELETE FROM recurring WHERE id=?", (rid,)), "Платёж удалён.")


# ---------------------------------------------------------------------
#  4. Кредитные карты
# ---------------------------------------------------------------------
def tab_cards(db):
    st.subheader("Кредитные карты")
    st.caption("Лимит, задолженность, обязательный платёж и дата следующего платежа")
    today = date.today()
    rows = db.q("SELECT * FROM credit_cards ORDER BY name")
    table, tl, td = [], 0.0, 0.0
    for r in rows:
        tl += r["credit_limit"]
        td += r["debt"]
        nd = next_date_from_day(r["pay_day"], today)
        delta = (nd - today).days
        if r["debt"] <= 0:
            status, tag = "Долга нет", "ok"
        elif delta == 0:
            status, tag = "Платить сегодня!", "overdue"
        elif delta <= 3:
            status, tag = f"Через {delta} дн.", "soon"
        else:
            status, tag = f"Через {delta} дн.", "ok"
        table.append([r["name"], fmt(r["credit_limit"]), fmt(r["debt"]), fmt(r["credit_limit"] - r["debt"]),
                      fmt(r["min_payment"]), r["pay_day"], nd.strftime("%d.%m.%Y"),
                      f"{status_icon(tag)} {status}"])
    c1, c2, c3 = st.columns(3)
    c1.metric("Общий долг", fmt(td))
    c2.metric("Общий лимит", fmt(tl))
    c3.metric("Доступно", fmt(tl - td))
    show_table(table, ["Карта", "Лимит", "Долг", "Доступно", "Обяз. платёж", "День", "След. платёж", "Статус"])

    with st.expander("➕ Добавить карту"):
        with st.form("card_add", clear_on_submit=True):
            name = st.text_input("Название")
            limit = st.number_input("Кредитный лимит", min_value=0.0, value=0.0, step=1000.0, format="%.2f")
            debt = st.number_input("Текущая задолженность", min_value=0.0, value=0.0, step=100.0, format="%.2f")
            minp = st.number_input("Обязательный платёж", min_value=0.0, value=0.0, step=100.0, format="%.2f")
            day = st.number_input("День платежа (1–31)", min_value=1, max_value=31, value=20, step=1)
            if st.form_submit_button("Добавить"):
                if not name.strip():
                    st.error("Укажите название карты")
                else:
                    db.x("INSERT INTO credit_cards(name, credit_limit, debt, min_payment, pay_day) "
                         "VALUES (?,?,?,?,?)", (name.strip(), limit, debt, minp, int(day)))
                    flash("Карта добавлена.")
                    st.rerun()
    if not rows:
        return
    card = pick("card_sel", "Выберите карту", rows, lambda x: f"{x['name']} — долг {fmt(x['debt'])}")
    cid = card["id"]
    with st.expander("✏️ Изменить"):
        with st.form(f"card_edit_{cid}"):
            name = st.text_input("Название", value=card["name"])
            limit = st.number_input("Кредитный лимит", min_value=0.0, value=float(card["credit_limit"]),
                                    step=1000.0, format="%.2f")
            debt = st.number_input("Текущая задолженность", min_value=0.0, value=float(card["debt"]),
                                   step=100.0, format="%.2f")
            minp = st.number_input("Обязательный платёж", min_value=0.0, value=float(card["min_payment"]),
                                   step=100.0, format="%.2f")
            day = st.number_input("День платежа (1–31)", min_value=1, max_value=31,
                                  value=int(card["pay_day"]), step=1)
            if st.form_submit_button("Сохранить"):
                if not name.strip():
                    st.error("Укажите название карты")
                else:
                    db.x("UPDATE credit_cards SET name=?, credit_limit=?, debt=?, min_payment=?, pay_day=? "
                         "WHERE id=?", (name.strip(), limit, debt, minp, int(day), cid))
                    flash("Карта обновлена.")
                    st.rerun()
    with st.expander("💳 Внести платёж"):
        accs = account_choices(db)
        if not accs:
            st.warning("Сначала создайте счёт")
        else:
            with st.form(f"card_pay_{cid}"):
                amount = st.number_input("Сумма платежа", min_value=0.0, value=float(card["min_payment"]),
                                         step=100.0, format="%.2f")
                acc = st.selectbox("Списать со счёта", list(accs))
                if st.form_submit_button("Внести платёж"):
                    if amount <= 0:
                        st.error("Сумма должна быть больше нуля")
                    else:
                        cat = db.ensure_category("Погашение кредитки", "expense")
                        db.add_transaction(today.isoformat(), "expense", amount, cat, accs[acc],
                                           f"Платёж по кредитной карте {card['name']}")
                        db.x("UPDATE credit_cards SET debt=? WHERE id=?", (max(0.0, card["debt"] - amount), cid))
                        flash(f"Платёж {fmt(amount)} по карте «{card['name']}» внесён.")
                        st.rerun()
    with st.expander("🗑 Удалить"):
        confirm_delete(f"card_{cid}", f"карту «{card['name']}»",
                       lambda: db.x("DELETE FROM credit_cards WHERE id=?", (cid,)), "Карта удалена.")


# ---------------------------------------------------------------------
#  5. Долги
# ---------------------------------------------------------------------
def tab_debts(db):
    st.subheader("Долги")
    st.caption("Кому вы должны и кто должен вам. Приближающиеся и просроченные сроки отмечены значками.")
    today = date.today()
    show_returned = st.checkbox("Показывать возвращённые", key="debt_show_all")
    all_rows = db.q("SELECT * FROM debts ORDER BY status, COALESCE(due_date, '9999-12-31'), person")
    table, shown, i_owe, owed_me, overdue, soon = [], [], 0.0, 0.0, 0, 0
    for r in all_rows:
        returned = r["status"] == "returned"
        if returned and not show_returned:
            continue
        shown.append(r)
        if returned:
            status, tag = "Возвращён", "done"
        else:
            if r["direction"] == "i_owe":
                i_owe += r["amount"]
            else:
                owed_me += r["amount"]
            if r["due_date"]:
                delta = (datetime.strptime(r["due_date"], "%Y-%m-%d").date() - today).days
                if delta < 0:
                    status, tag = f"Просрочено на {-delta} дн.", "overdue"
                    overdue += 1
                elif delta == 0:
                    status, tag = "Срок — сегодня!", "overdue"
                    overdue += 1
                elif delta <= 3:
                    status, tag = f"Через {delta} дн.", "soon"
                    soon += 1
                else:
                    status, tag = f"Через {delta} дн.", "ok"
            else:
                status, tag = "Без срока", "ok"
        table.append([r["person"], r["contact"], "Я должен" if r["direction"] == "i_owe" else "Мне должны",
                      fmt(r["amount"]), fmt_iso(r["date"]), fmt_iso(r["due_date"]),
                      f"{status_icon(tag)} {status}"])
    if overdue:
        st.error(f"⚠ Просрочено: {overdue} · Срок в ближайшие 3 дня: {soon}")
    elif soon:
        st.warning(f"⚠ Срок в ближайшие 3 дня: {soon}")
    else:
        st.success("Срочных долгов нет")
    c1, c2 = st.columns(2)
    c1.metric("Я должен (активные)", fmt(i_owe))
    c2.metric("Мне должны (активные)", fmt(owed_me))
    show_table(table, ["Имя", "Контакт / заметка", "Тип", "Сумма", "Дата", "Вернуть до", "Статус"])

    with st.expander("➕ Добавить долг"):
        with st.form("debt_add", clear_on_submit=True):
            person = st.text_input("Имя человека")
            contact = st.text_input("Контакт (телефон/заметка)")
            amount = st.number_input("Сумма", min_value=0.0, value=0.0, step=100.0, format="%.2f")
            direction = st.selectbox("Тип", list(DIRS))
            d_date = st.date_input("Дата", value=today, format="DD.MM.YYYY")
            has_due = st.checkbox("Указать срок возврата")
            d_due = st.date_input("Вернуть до", value=today, format="DD.MM.YYYY")
            if st.form_submit_button("Добавить"):
                if not person.strip():
                    st.error("Укажите имя человека")
                elif amount <= 0:
                    st.error("Сумма должна быть больше нуля")
                else:
                    db.x("INSERT INTO debts(person, contact, amount, direction, date, due_date) "
                         "VALUES (?,?,?,?,?,?)",
                         (person.strip(), contact.strip(), amount, DIRS[direction], d_date.isoformat(),
                          d_due.isoformat() if has_due else None))
                    flash("Долг добавлен.")
                    st.rerun()
    if not shown:
        return
    r = pick("debt_sel", "Выберите запись", shown,
             lambda x: f"{x['person']} — {fmt(x['amount'])} ({'я должен' if x['direction'] == 'i_owe' else 'мне должны'})"
                       f"{' · возвращён' if x['status'] == 'returned' else ''}")
    did = r["id"]
    returned = r["status"] == "returned"
    if st.button("↩ Отметить как не возвращённый" if returned else "✔ Отметить как возвращённый",
                 key=f"debt_toggle_{did}"):
        db.x("UPDATE debts SET status=? WHERE id=?", ("active" if returned else "returned", did))
        flash("Статус долга изменён.")
        st.rerun()
    with st.expander("✏️ Изменить"):
        with st.form(f"debt_edit_{did}"):
            person = st.text_input("Имя человека", value=r["person"])
            contact = st.text_input("Контакт (телефон/заметка)", value=r["contact"])
            amount = st.number_input("Сумма", min_value=0.0, value=float(r["amount"]), step=100.0, format="%.2f")
            names = list(DIRS)
            direction = st.selectbox("Тип", names, index=0 if r["direction"] == "i_owe" else 1)
            d_date = st.date_input("Дата", value=datetime.strptime(r["date"], "%Y-%m-%d").date(),
                                   format="DD.MM.YYYY")
            has_due = st.checkbox("Указать срок возврата", value=bool(r["due_date"]))
            d_due = st.date_input("Вернуть до",
                                  value=datetime.strptime(r["due_date"], "%Y-%m-%d").date() if r["due_date"] else today,
                                  format="DD.MM.YYYY")
            if st.form_submit_button("Сохранить"):
                if not person.strip():
                    st.error("Укажите имя человека")
                elif amount <= 0:
                    st.error("Сумма должна быть больше нуля")
                else:
                    db.x("UPDATE debts SET person=?, contact=?, amount=?, direction=?, date=?, due_date=? WHERE id=?",
                         (person.strip(), contact.strip(), amount, DIRS[direction], d_date.isoformat(),
                          d_due.isoformat() if has_due else None, did))
                    flash("Запись обновлена.")
                    st.rerun()
    with st.expander("🗑 Удалить"):
        confirm_delete(f"debt_{did}", f"запись о долге ({r['person']})",
                       lambda: db.x("DELETE FROM debts WHERE id=?", (did,)), "Запись удалена.")


# ---------------------------------------------------------------------
#  6. Бюджет
# ---------------------------------------------------------------------
def tab_budget(db):
    today = date.today()
    month = month_key(today)
    st.subheader(f"Бюджет на {month_title(today)}")
    limits = db.budget_limits()
    cats = db.categories("expense")
    total_limit = total_spent_limited = 0.0
    for cat in cats:
        spent = db.spent(cat["id"], month)
        limit = limits.get(cat["id"])
        c1, c2 = st.columns([1, 3])
        c1.markdown(f"**{cat['name']}**")
        if limit:
            total_limit += limit
            total_spent_limited += spent
            ratio = spent / limit
            c2.progress(min(1.0, ratio))
            icon = "🔴" if ratio > 1 else ("🟠" if ratio >= 0.8 else "🟢")
            if spent > limit:
                c2.caption(f"{icon} {fmt(spent)} из {fmt(limit)} · превышено на {fmt(spent - limit)}")
            else:
                c2.caption(f"{icon} {fmt(spent)} из {fmt(limit)} · осталось {fmt(limit - spent)}")
        else:
            c2.progress(0.0)
            c2.caption(f"Потрачено {fmt(spent)} · лимит не задан")
    income, expense = db.month_totals(month)
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("Доходы за месяц", fmt(income))
    c2.metric("Запланировано (лимиты)", fmt(total_limit))
    c3.metric("Свободно к распределению", fmt(income - total_limit))
    st.caption(f"Потрачено по категориям с лимитом: {fmt(total_spent_limited)} · Всего расходов: {fmt(expense)}")

    if cats:
        with st.expander("🎯 Задать лимит на месяц", expanded=True):
            cat = pick("bud_sel", "Категория", cats, lambda c: c["name"])
            cur = limits.get(cat["id"])
            with st.form(f"bud_form_{cat['id']}"):
                v = st.number_input("Бюджет на месяц (0 — убрать лимит)", min_value=0.0,
                                    value=float(cur) if cur else 0.0, step=100.0, format="%.2f")
                if st.form_submit_button("Сохранить"):
                    if v == 0:
                        db.x("DELETE FROM budgets WHERE category_id=?", (cat["id"],))
                    else:
                        db.x("INSERT INTO budgets(category_id, limit_amount) VALUES (?,?) "
                             "ON CONFLICT(category_id) DO UPDATE SET limit_amount=excluded.limit_amount",
                             (cat["id"], v))
                    flash("Лимит сохранён.")
                    st.rerun()


# ---------------------------------------------------------------------
#  7. Категории
# ---------------------------------------------------------------------
def category_panel(db, kind, title):
    st.markdown(f"#### {title}")
    rows = db.categories(kind)
    show_table([[c["name"], c["keywords"]] for c in rows], ["Название", "Ключевые слова (для умного ввода)"])
    with st.expander("➕ Добавить"):
        with st.form(f"cat_add_{kind}", clear_on_submit=True):
            name = st.text_input("Название")
            kw = st.text_input("Ключевые слова через запятую")
            if st.form_submit_button("Добавить"):
                if not name.strip():
                    st.error("Укажите название категории")
                else:
                    try:
                        db.x("INSERT INTO categories(name, type, keywords) VALUES (?,?,?)",
                             (name.strip(), kind, kw.strip()))
                    except sqlite3.IntegrityError:
                        st.error("Такая категория уже существует")
                    else:
                        flash("Категория добавлена.")
                        st.rerun()
    if not rows:
        return
    c = pick(f"cat_sel_{kind}", "Выберите категорию", rows, lambda x: x["name"])
    cid = c["id"]
    with st.expander("✏️ Изменить"):
        with st.form(f"cat_edit_{kind}_{cid}"):
            name = st.text_input("Название", value=c["name"])
            kw = st.text_input("Ключевые слова через запятую", value=c["keywords"] or "")
            if st.form_submit_button("Сохранить"):
                if not name.strip():
                    st.error("Укажите название категории")
                else:
                    try:
                        db.x("UPDATE categories SET name=?, keywords=? WHERE id=?", (name.strip(), kw.strip(), cid))
                    except sqlite3.IntegrityError:
                        st.error("Такая категория уже существует")
                    else:
                        flash("Категория обновлена.")
                        st.rerun()
    with st.expander("🗑 Удалить"):
        st.caption("Операции останутся, но будут без категории.")
        confirm_delete(f"cat_{kind}_{cid}", f"категорию «{c['name']}»",
                       lambda: db.x("DELETE FROM categories WHERE id=?", (cid,)), "Категория удалена.")


def tab_categories(db):
    st.subheader("Категории")
    st.caption("Свои категории доходов и расходов. Ключевые слова помогают умному вводу находить категорию.")
    left, right = st.columns(2)
    with left:
        category_panel(db, "expense", "Расходы")
    with right:
        category_panel(db, "income", "Доходы")


# ---------------------------------------------------------------------
#  8. Аналитика
# ---------------------------------------------------------------------
def tab_analytics(db):
    st.subheader("Аналитика")
    months = last_months(12)
    month = st.selectbox("Месяц", months, key="an_month")
    income, expense = db.month_totals(month)
    c1, c2, c3 = st.columns(3)
    c1.metric("Доходы", fmt(income))
    c2.metric("Расходы", fmt(expense))
    c3.metric("Итог", fmt(income - expense))

    data = db.expense_by_category(month)
    ms = months_before(month, 6)
    inc = [db.month_totals(m)[0] for m in ms]
    exp = [db.month_totals(m)[1] for m in ms]
    labels = [f"{m[5:7]}.{m[:4]}" for m in ms]

    if HAS_MPL:
        fig = Figure(figsize=(11, 4.6), dpi=100)
        ax1 = fig.add_subplot(1, 2, 1)
        ax2 = fig.add_subplot(1, 2, 2)
        if data:
            ax1.pie([r["total"] for r in data], labels=[r["name"] for r in data], startangle=90,
                    autopct=lambda p: f"{p:.0f}%" if p >= 4 else "", textprops={"fontsize": 9})
        else:
            ax1.text(0.5, 0.5, "Нет расходов за этот месяц", ha="center", va="center")
            ax1.axis("off")
        ax1.set_title("Расходы по категориям")
        xs = list(range(len(ms)))
        w = 0.38
        ax2.bar([x - w / 2 for x in xs], inc, w, label="Доходы", color="#4caf50")
        ax2.bar([x + w / 2 for x in xs], exp, w, label="Расходы", color="#e57373")
        ax2.set_xticks(xs)
        ax2.set_xticklabels(labels)
        ax2.set_title("Доходы и расходы (6 месяцев)")
        ax2.legend()
        fig.tight_layout()
        st.pyplot(fig)
    else:
        st.info("Установите matplotlib (pip install matplotlib) для диаграмм — пока показаны простые графики.")
        st.markdown("**Расходы по категориям**")
        if data:
            st.bar_chart(pd.DataFrame({"Расходы": [r["total"] for r in data]}, index=[r["name"] for r in data]))
        else:
            st.caption("Нет расходов за этот месяц")
        st.markdown("**Доходы и расходы (6 месяцев)**")
        st.bar_chart(pd.DataFrame({"Доходы": inc, "Расходы": exp}, index=labels))


# ---------------------------------------------------------------------
#  Запуск
# ---------------------------------------------------------------------
def main():
    st.set_page_config(page_title=APP_TITLE, page_icon="💰", layout="wide")
    st.title(f"💰 {APP_TITLE}")
    show_flash()
    db = Database()  # новое соединение на каждый запуск скрипта — безопасно для потоков Streamlit
    try:
        names = ["Умный ввод", "Счета", "Регулярные платежи", "Кредитные карты",
                 "Долги", "Бюджет", "Категории", "Аналитика"]
        funcs = [tab_input, tab_accounts, tab_recurring, tab_cards,
                 tab_debts, tab_budget, tab_categories, tab_analytics]
        for tab, fn in zip(st.tabs(names), funcs):
            with tab:
                fn(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()