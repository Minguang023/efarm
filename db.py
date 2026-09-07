"""One database for every month. Money is stored as whole Malaysian sen."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Column, Date, Integer,
                        MetaData, String, Table, Text, create_engine, insert, select,
                        update, text)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

CATEGORIES = ('Stock', 'Salary/EPF/Accounting', 'Rental', 'Bills')
MAX_RM = Decimal('99999999.99')
metadata = MetaData()
sales = Table('daily_sales', metadata,
    Column('sale_date', Date, primary_key=True),
    Column('sales_1_sen', BigInteger, nullable=False),
    Column('sales_2_sen', BigInteger, nullable=False),
    Column('e_wallet_sen', BigInteger, nullable=False),
    Column('version', Integer, nullable=False, default=1),
    Column('updated_at', String(40), nullable=False),
    CheckConstraint('sales_1_sen >= 0 AND sales_2_sen >= 0 AND e_wallet_sen >= 0'))
expenses = Table('expenses', metadata,
    Column('id', String(36), primary_key=True),
    Column('invoice_date', Date, nullable=False),
    Column('reporting_month', Date, nullable=False, index=True),
    Column('invoice_code', String(120), nullable=False),
    Column('description', Text, nullable=False),
    Column('category', String(40), nullable=False),
    Column('amount_sen', BigInteger, nullable=False),
    Column('voided', Boolean, nullable=False, default=False),
    Column('version', Integer, nullable=False, default=1),
    Column('updated_at', String(40), nullable=False),
    CheckConstraint('amount_sen > 0'),
    CheckConstraint("category IN ('Stock', 'Salary/EPF/Accounting', 'Rental', 'Bills')"))

# Separate legacy table preserves incomplete source rows without weakening new-entry validation.
historical_expenses = Table('historical_expenses', metadata,
    Column('id', String(36), primary_key=True), Column('invoice_date', Date),
    Column('reporting_month', Date, nullable=False, index=True),
    Column('invoice_code', Text, nullable=False), Column('description', Text, nullable=False),
    Column('category', String(40)), Column('amount_sen', BigInteger),
    Column('voided', Boolean, nullable=False, default=False), Column('version', Integer, nullable=False, default=1),
    Column('updated_at', String(40), nullable=False), Column('source_sheet', Text, nullable=False),
    Column('source_row', Integer, nullable=False), Column('legacy_code', Text, nullable=False),
    Column('source_json', Text, nullable=False), Column('review_note', Text, nullable=False))
workbook_summaries = Table('workbook_summaries', metadata,
    Column('reporting_month', Date, primary_key=True), Column('payload_json', Text, nullable=False))
import_batches = Table('import_batches', metadata,
    Column('id', String(64), primary_key=True), Column('report_json', Text, nullable=False),
    Column('imported_at', String(40), nullable=False))
invoice_numbers = Table('invoice_numbers', metadata,
    Column('number', Integer, primary_key=True, autoincrement=True),
    Column('invoice_id', String(36), nullable=False, unique=True),
    sqlite_autoincrement=True)

class Conflict(ValueError):
    """A record was already saved or changed in another session."""

def to_sen(value):
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Enter a valid RM amount.') from None
    if not number.is_finite() or number < 0 or number > MAX_RM:
        raise ValueError(f'Amount must be between RM0.00 and RM{MAX_RM}.')
    if number != number.quantize(Decimal('0.01')):
        raise ValueError('Use at most two decimal places.')
    return int(number * 100)

def rm(sen):
    return f'RM{Decimal(int(sen)) / 100:,.2f}'

def month_bounds(year, month):
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def connect(url):
    options = {'pool_pre_ping': True, 'hide_parameters': True}
    if url.startswith('sqlite'):
        options['connect_args'] = {'check_same_thread': False, 'timeout': 20}
    else:
        # A small app does not need to hold idle database connections open.
        options['poolclass'] = NullPool
        options['connect_args'] = {'connect_timeout': 10}
    engine = create_engine(url, **options)
    metadata.create_all(engine)
    if engine.dialect.name == 'postgresql':
        # Supabase exposes public-schema tables through a separate Data API.
        # No API policies are created: only the server's table-owner connection writes.
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE daily_sales ENABLE ROW LEVEL SECURITY'))
            conn.execute(text('ALTER TABLE expenses ENABLE ROW LEVEL SECURITY'))
            for table in ('historical_expenses','workbook_summaries','import_batches','invoice_numbers'):
                conn.execute(text(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY'))
    return engine

def allocate_numbers(conn, ids):
    if not ids: return
    if conn.dialect.name=='postgresql':
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    statement=dialect_insert(invoice_numbers).on_conflict_do_nothing(index_elements=['invoice_id'])
    for start in range(0,len(ids),500):
        conn.execute(statement,[{'invoice_id':key} for key in ids[start:start+500]])

def ensure_invoice_numbers(engine):
    with engine.begin() as conn:
        known=set(conn.execute(select(invoice_numbers.c.invoice_id)).scalars())
        ids=set()
        for table in (expenses,historical_expenses):
            ids.update(conn.execute(select(table.c.id)).scalars())
        allocate_numbers(conn,sorted(ids-known))

def invoice_label(engine,record_id):
    with engine.connect() as conn:
        number=conn.execute(select(invoice_numbers.c.number).where(invoice_numbers.c.invoice_id==record_id)).scalar_one()
    return f'INV-{number:05d}'

def description_options(engine):
    with engine.connect() as conn:
        values=[]
        for table in (expenses,historical_expenses):
            values.extend(conn.execute(select(table.c.description).distinct()).scalars())
    options={}
    for value in sorted(values):
        value=value.strip()
        if value: options.setdefault(value.casefold(),value)
    return sorted(options.values(),key=str.casefold)

def get_sale(engine, day):
    with engine.connect() as conn:
        row = conn.execute(select(sales).where(sales.c.sale_date == day)).mappings().first()
        return dict(row) if row else None

def save_sale(engine, day, amounts, expected_version=None):
    if not isinstance(day, date) or len(amounts) != 3:
        raise ValueError('Supply a date and three payment amounts.')
    values = dict(zip(('sales_1_sen', 'sales_2_sen', 'e_wallet_sen'), map(to_sen, amounts)))
    values['updated_at'] = timestamp()
    try:
        with engine.begin() as conn:
            if expected_version is None:
                conn.execute(insert(sales).values(sale_date=day, version=1, **values))
            else:
                result = conn.execute(update(sales).where(
                    sales.c.sale_date == day, sales.c.version == expected_version
                ).values(version=expected_version + 1, **values))
                if result.rowcount != 1:
                    raise Conflict('This day changed in another session. Refresh before saving again.')
    except IntegrityError:
        raise Conflict('Sales already exist for this date. Refresh and use Edit saved day.') from None

def expense_values(invoice_date, reporting_month, invoice_code, description, category, amount):
    code, desc = str(invoice_code or '').strip(), str(description or '').strip()
    if not isinstance(invoice_date, date) or not isinstance(reporting_month, date):
        raise ValueError('Choose valid dates.')
    if not code or len(code) > 120:
        raise ValueError('Enter an invoice code (maximum 120 characters).')
    if not desc or len(desc) > 2000:
        raise ValueError('Enter a description (maximum 2,000 characters).')
    if category not in CATEGORIES:
        raise ValueError('Choose a valid expense category.')
    amount_sen = to_sen(amount)
    if amount_sen == 0:
        raise ValueError('An expense must be greater than RM0.00.')
    return dict(invoice_date=invoice_date, reporting_month=reporting_month.replace(day=1),
                invoice_code=code, description=desc, category=category,
                amount_sen=amount_sen, updated_at=timestamp())

def add_expense(engine, invoice_date, reporting_month, invoice_code, description, category, amount, record_id=None):
    values = expense_values(invoice_date, reporting_month, invoice_code, description, category, amount)
    record_id = record_id or str(uuid4())
    try:
        with engine.begin() as conn:
            conn.execute(insert(expenses).values(id=record_id, version=1, voided=False, **values))
            allocate_numbers(conn,[record_id])
    except IntegrityError:
        raise Conflict('This submission has already been saved. Refresh to start another expense.') from None
    return record_id

def edit_expense(engine, record_id, expected_version, **kwargs):
    values = expense_values(**kwargs)
    with engine.begin() as conn:
        table = expense_table(conn,record_id)
        if table is historical_expenses: values['review_note']=''
        result = conn.execute(update(table).where(
            table.c.id == record_id, table.c.version == expected_version,
            table.c.voided.is_(False)
        ).values(version=expected_version + 1, **values))
        if result.rowcount != 1:
            raise Conflict('This expense changed in another session. Refresh before saving again.')

def void_expense(engine, record_id, expected_version):
    with engine.begin() as conn:
        table = expense_table(conn,record_id)
        result = conn.execute(update(table).where(
            table.c.id == record_id, table.c.version == expected_version,
            table.c.voided.is_(False)
        ).values(voided=True, version=expected_version + 1, updated_at=timestamp()))
        if result.rowcount != 1:
            raise Conflict('This expense changed in another session. Refresh first.')

def get_expense(engine, record_id):
    with engine.connect() as conn:
        table = expense_table(conn,record_id)
        row = conn.execute(select(table).where(table.c.id == record_id)).mappings().first()
        return dict(row) if row else None

def expense_table(conn,record_id):
    if conn.execute(select(historical_expenses.c.id).where(historical_expenses.c.id==record_id)).first():
        return historical_expenses
    return expenses

def monthly_records(engine, year, month):
    start, end = month_bounds(year, month)
    # One transaction gives a consistent snapshot for the two tables in PostgreSQL.
    with engine.connect() as conn:
        if engine.dialect.name == 'postgresql':
            conn = conn.execution_options(isolation_level='REPEATABLE READ')
        with conn.begin():
            s = conn.execute(select(sales).where(sales.c.sale_date >= start,
                sales.c.sale_date < end).order_by(sales.c.sale_date)).mappings().all()
            e = conn.execute(select(expenses).where(expenses.c.reporting_month == start,
                expenses.c.voided.is_(False)).order_by(expenses.c.invoice_date, expenses.c.id)).mappings().all()
            e += conn.execute(select(historical_expenses).where(historical_expenses.c.reporting_month==start,
                historical_expenses.c.voided.is_(False))).mappings().all()
            numbers=dict(conn.execute(select(invoice_numbers.c.invoice_id,invoice_numbers.c.number)).all())
    return [dict(row) for row in s], [dict(row,display_id=f'INV-{numbers[row["id"]]:05d}' if row['id'] in numbers else row['id']) for row in e]

def summarize(sale_rows, expense_rows):
    debit = {label: sum(r[key] for r in sale_rows) for label, key in
        [('Sales 1', 'sales_1_sen'), ('Sales 2', 'sales_2_sen'), ('E-Wallet', 'e_wallet_sen')]}
    credit = {category: sum(r['amount_sen'] or 0 for r in expense_rows if r['category'] == category)
              for category in CATEGORIES}
    unclassified=[r for r in expense_rows if r['category'] not in CATEGORIES]
    if unclassified: credit['Uncategorised']=sum(r['amount_sen'] or 0 for r in unclassified)
    total_sales, total_expenses = sum(debit.values()), sum(credit.values())
    return dict(debit=debit, credit=credit, total_sales=total_sales,
                total_expenses=total_expenses, profit=total_sales-total_expenses,
                missing_amounts=sum(r['amount_sen'] is None for r in expense_rows),
                unclassified=len(unclassified))

def all_records(engine):
    with engine.connect() as conn:
        return {t.name: [dict(row) for row in conn.execute(select(t)).mappings()]
                for t in (sales, expenses,historical_expenses,workbook_summaries,import_batches,invoice_numbers)}

def available_periods(engine):
    with engine.connect() as conn:
        periods={d.replace(day=1) for d in conn.execute(select(sales.c.sale_date)).scalars()}
        for table in (expenses,historical_expenses):
            periods.update(conn.execute(select(table.c.reporting_month).distinct()).scalars())
    return sorted(periods)

def year_trend(engine,year):
    start,end=date(year,1,1),date(year+1,1,1)
    with engine.connect() as conn:
        s=[dict(r) for r in conn.execute(select(sales).where(sales.c.sale_date>=start,sales.c.sale_date<end)).mappings()]
        e=[]
        for table in (expenses,historical_expenses):
            e += [dict(r) for r in conn.execute(select(table).where(table.c.reporting_month>=start,
                    table.c.reporting_month<end,table.c.voided.is_(False))).mappings()]
    output=[]
    for month in range(1,13):
        sr=[r for r in s if r['sale_date'].month==month];er=[r for r in e if r['reporting_month'].month==month]
        if sr or er: output.append(dict(month=date(year,month,1),**summarize(sr,er)))
    return output

def source_summary(engine,period):
    import json
    with engine.connect() as conn:
        payload=conn.execute(select(workbook_summaries.c.payload_json).where(workbook_summaries.c.reporting_month==period)).scalar()
    return json.loads(payload) if payload else None

def assign_categories(engine, records, category):
    if category not in CATEGORIES: raise ValueError('Choose a category.')
    if not records: raise ValueError('Choose at least one invoice.')
    with engine.begin() as conn:
        for record in records:
            table=expense_table(conn,record['id'])
            changed=conn.execute(update(table).where(table.c.id==record['id'],table.c.version==record['version'],
                table.c.voided.is_(False)).values(category=category,version=record['version']+1,updated_at=timestamp()))
            if changed.rowcount!=1: raise Conflict('An invoice changed. Refresh and select the invoices again; no categories were changed.')
