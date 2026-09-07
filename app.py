import calendar
import hmac
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

import db
import ui
from history_import import import_history

st.set_page_config(page_title='E FARM 森苑饭庄 | Business records', page_icon='🌿', layout='wide')
ui.style()

def setting(name, default=''):
    if name in os.environ:
        return os.environ[name]
    try:
        return st.secrets.get(name, default)
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        return default

def today():
    return datetime.now(ZoneInfo('Asia/Kuala_Lumpur')).date()

local_demo = str(setting('LOCAL_DEMO', 'false')).lower() == 'true'
password = str(setting('APP_PASSWORD'))
database_url = str(setting('DATABASE_URL'))
if not local_demo and (not password or not database_url.startswith('postgresql+psycopg://')):
    st.title('E FARM · Setup')
    st.info('Add APP_PASSWORD and a PostgreSQL DATABASE_URL in Streamlit Secrets. See README.md in the project for the setup steps. For a local trial, set LOCAL_DEMO=true.')
    st.stop()

if password and not st.session_state.get('authenticated'):
    st.title('E FARM')
    st.caption('Restaurant records · Sign in')
    with st.form('login'):
        entered = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Sign in', type='primary')
    if submitted:
        if hmac.compare_digest(entered.encode(), password.encode()):
            st.session_state['authenticated'] = True
            st.rerun()
        st.error('Incorrect password.')
    st.stop()

@st.cache_resource
def database(url, auto_import):
    engine = db.connect(url)
    if auto_import:
        import_history(engine)
    db.ensure_invoice_numbers(engine)
    return engine

try:
    if not database_url:
        data_dir = Path(__file__).parent / 'data'
        data_dir.mkdir(exist_ok=True)
        database_url = f'sqlite:///{data_dir / "restaurant.db"}'
    engine = database(database_url, str(setting('AUTO_IMPORT_HISTORY','true')).lower()=='true')
except SQLAlchemyError:
    st.error('Cannot connect to the database. Check your connection settings and try again.')
    st.stop()

periods = db.available_periods(engine)
default_period = periods[-1] if periods else today().replace(day=1)
with st.sidebar:
    st.title('E FARM')
    st.markdown('森苑饭庄')
    st.caption('RESTAURANT RECORDS')
    page = st.radio('Workspace', ['Dashboard', 'Daily sales', 'Expenses', 'Monthly summary', 'Export records'], key='workspace')
    st.divider()
    year = int(st.number_input('Year', min_value=2000, max_value=2100, value=default_period.year, step=1))
    month = st.selectbox('Month', list(range(1, 13)), index=default_period.month-1,
                         format_func=lambda n: calendar.month_name[n])
    st.caption('Dates use Malaysia time (UTC+8).')
    if password and st.button('Sign out', key='sign_out'):
        st.session_state.clear()
        st.rerun()

period = date(year, month, 1)
period_label = period.strftime('%B %Y')
if message := st.session_state.pop('flash', None):
    st.success(message)

def saved(message):
    for key in list(st.session_state):
        if key.startswith('snapshot_'):
            del st.session_state[key]
    st.session_state['flash'] = message
    st.rerun()

def run_write(operation, message):
    try:
        operation()
    except (ValueError, db.Conflict) as exc:
        st.error(str(exc))
        return False
    except SQLAlchemyError:
        st.error('The save could not be confirmed. Refresh records before retrying.')
        return False
    saved(message)

def sales_frame(rows):
    return pd.DataFrame([{'Date': r['sale_date'], 'Sales 1': r['sales_1_sen']/100,
        'Sales 2': r['sales_2_sen']/100, 'E-Wallet': r['e_wallet_sen']/100,
        'Total daily sales': (r['sales_1_sen']+r['sales_2_sen']+r['e_wallet_sen'])/100}
        for r in rows], columns=['Date','Sales 1','Sales 2','E-Wallet','Total daily sales'])

def expenses_frame(rows):
    return pd.DataFrame([{'ID': r.get('display_id',r['id']), 'Invoice date': r['invoice_date'],
        'Reporting month': str(r['reporting_month'])[:7], 'Invoice code': r['invoice_code'],
        'Description': r['description'], 'Category': r['category'] or 'Uncategorised', 'Amount': r['amount_sen']/100 if r['amount_sen'] is not None else None}
        for r in rows], columns=['ID','Invoice date','Reporting month','Invoice code','Description','Category','Amount'])

def show_money_table(frame, money_columns):
    st.dataframe(frame, hide_index=True, width='stretch', column_config={
        key: st.column_config.NumberColumn(key + ' (RM)', format='%.2f') for key in money_columns})

def metrics(summary):
    a,b,c = st.columns(3)
    a.metric('Total sales', db.rm(summary['total_sales']))
    b.metric('Total expenses', db.rm(summary['total_expenses']))
    c.metric('Profit / loss', db.rm(summary['profit']))

def summary_frame(summary):
    rows = [{'Description': k, 'Debit': v/100, 'Credit': None} for k,v in summary['debit'].items()]
    rows += [{'Description': k, 'Debit': None, 'Credit': v/100} for k,v in summary['credit'].items()]
    rows += [{'Description':'TOTAL', 'Debit':summary['total_sales']/100, 'Credit':summary['total_expenses']/100}]
    return pd.DataFrame(rows)

try:
    sale_rows, expense_rows = db.monthly_records(engine, year, month)
except SQLAlchemyError:
    st.error('Could not load records. Check your database connection and refresh.')
    st.stop()
summary = db.summarize(sale_rows, expense_rows)

if page == 'Dashboard':
    ui.dashboard(engine,period,sale_rows,expense_rows,summary,sales_frame,expenses_frame)

elif page == 'Daily sales':
    st.title('Daily sales')
    mode = st.radio('Entry mode', ['Today', 'Edit saved day / enter a missed day'], horizontal=True)
    day = today() if mode == 'Today' else st.date_input('Sales date', value=min(today(),period),
                        min_value=date(2000,1,1), max_value=today())
    st.caption(day.strftime('%A, %d %B %Y') + ' · Malaysia time')
    snapshot_key = f'snapshot_sale_{day}'
    if snapshot_key not in st.session_state:
        st.session_state[snapshot_key] = db.get_sale(engine, day)
    existing = st.session_state[snapshot_key]
    if existing:
        st.info('A record already exists. Saving replaces the three daily totals; it does not add another record.')
    version = existing['version'] if existing else None
    # Retain the version originally shown, even if another session updates the record.
    with st.container(border=True):
        cols = st.columns(3)
        amounts = [cols[i].number_input(label+' (RM)', min_value=0.0, max_value=float(db.MAX_RM),
            value=(existing[key]/100 if existing else 0.0), step=0.01, format='%.2f', key=f'{day}_{version}_{key}')
            for i,(label,key) in enumerate([('Sales 1','sales_1_sen'),('Sales 2','sales_2_sen'),('E-Wallet','e_wallet_sen')])]
        st.metric('Total daily sales', db.rm(sum(db.to_sen(a) for a in amounts)))
        confirmed = st.checkbox('Replace the saved amounts for this day', key=f'confirm_{day}_{version}') if existing else True
        submitted = st.button('Update daily sales' if existing else 'Save daily sales', type='primary', key=f'save_{day}_{version}')
    if submitted:
        if mode == 'Today' and today() != day:
            st.error('The date changed at midnight. Refresh before saving.')
        elif not confirmed:
            st.error('Confirm that you want to replace this day’s amounts.')
        else:
            run_write(lambda: db.save_sale(engine, day, amounts, version),
                      f'Saved {day}: {db.rm(sum(db.to_sen(a) for a in amounts))}.')
    if existing:
        st.metric('Saved total for this day', db.rm(sum(existing[k] for k in ['sales_1_sen','sales_2_sen','e_wallet_sen'])))
    st.subheader(period_label + ' records')
    show_money_table(sales_frame(sale_rows), ['Sales 1','Sales 2','E-Wallet','Total daily sales'])
    st.metric('Monthly sales', db.rm(summary['total_sales']))

elif page == 'Expenses':
    st.title('Expenses')
    st.caption('Each invoice receives a unique ID. Its reporting month determines which summary includes it.')
    search_col,filter_col = st.columns([2,1])
    search = search_col.text_input('Search invoices',placeholder='Supplier, description, invoice code or old code…')
    filter_category = filter_col.selectbox('Show category',['All',*db.CATEGORIES,'Uncategorised','Data issues'])
    visible_rows=[r for r in expense_rows if (not search or search.casefold() in ' '.join(str(r.get(k) or '') for k in ('description','invoice_code','legacy_code')).casefold())
        and (filter_category=='All' or (filter_category=='Uncategorised' and not r['category'])
        or (filter_category=='Data issues' and bool(r.get('review_note'))) or r['category']==filter_category)]
    action = st.radio('Action', ['Add invoice','Edit / void invoice','Assign categories'], horizontal=True)
    record = None
    if action == 'Edit / void invoice':
        if not visible_rows:
            st.info('No invoices to edit in the selected month.')
        else:
            by_id = {r['id']: r for r in visible_rows}
            selected = st.selectbox('Choose invoice', list(by_id), format_func=lambda key:
                f"{by_id[key].get('display_id',key)} · {by_id[key]['invoice_date']} · {by_id[key]['invoice_code']} · {db.rm(by_id[key]['amount_sen']) if by_id[key]['amount_sen'] is not None else 'Amount missing'}")
            snapshot_key = f'snapshot_expense_{selected}'
            if snapshot_key not in st.session_state:
                st.session_state[snapshot_key] = by_id[selected]
            record = st.session_state[snapshot_key]
            st.caption('ID: ' + record.get('display_id',record['id']))
            if record.get('source_sheet'):
                with st.expander('Original workbook record'):
                    st.write(f"{record['source_sheet']} · row {record['source_row']} · code {record['legacy_code']}")
                    st.write(json.loads(record['source_json']))
                    if record.get('review_note'): st.warning(record['review_note'])
    if action == 'Add invoice' or record:
        if action == 'Add invoice' and 'expense_token' not in st.session_state:
            st.session_state.expense_token = str(uuid4())
        token = record['id'] + '_' + str(record['version']) if record else st.session_state.expense_token
        with st.form('expense_' + token, clear_on_submit=False):
            left,right = st.columns(2)
            inv_date = left.date_input('Invoice date', value=(record['invoice_date'] or record['reporting_month']) if record else today(),
                                      min_value=date(1900,1,1), max_value=max(today(),record['invoice_date'] or today()) if record else today())
            report_date = right.date_input('Reporting month (any day in that month)',
                value=record['reporting_month'] if record else period, min_value=date(2000,1,1), max_value=date(2100,12,31))
            code = left.text_input('Invoice code', value=record['invoice_code'] if record else '', max_chars=120,
                help='Letters, numbers, slashes and hyphens are accepted. For an expense without an invoice, enter a meaningful internal reference.')
            category = right.selectbox('Category', db.CATEGORIES,
                index=db.CATEGORIES.index(record['category']) if record and record['category'] in db.CATEGORIES else None, placeholder='Choose category')
            descriptions=db.description_options(engine)
            current=record['description'] if record else None
            if current and current not in descriptions: descriptions.insert(0,current)
            desc = st.selectbox('Description',descriptions,
                index=descriptions.index(current) if current else None,
                accept_new_options=True,filter_mode='contains',
                placeholder='Type a keyword or add a new description',
                help='Search previous descriptions from all months. Select a match, or type a new description and press Enter. New descriptions become suggestions after the invoice is saved.')
            amount = st.number_input('Amount (RM)', min_value=0.0, max_value=float(db.MAX_RM),
                value=max(0,(record['amount_sen'] or 0)/100) if record else 0.0, step=0.01, format='%.2f')
            submit = st.form_submit_button('Save changes' if record else 'Save invoice', type='primary')
        if submit:
            values = dict(invoice_date=inv_date, reporting_month=report_date, invoice_code=code,
                          description=desc, category=category, amount=amount)
            if record:
                run_write(lambda: db.edit_expense(engine, record['id'], record['version'], **values), 'Invoice updated.')
            else:
                try:
                    new_id = db.add_expense(engine, **values, record_id=st.session_state.expense_token)
                except (ValueError, db.Conflict) as exc:
                    st.error(str(exc))
                except SQLAlchemyError:
                    st.error('The save could not be confirmed. Refresh records before retrying.')
                else:
                    del st.session_state['expense_token']
                    saved(f'Invoice saved for {report_date:%B %Y}. ID: {db.invoice_label(engine,new_id)}')
        if record:
            with st.expander('Void this invoice'):
                st.caption('The record stays in the database but is excluded from totals. This action cannot be undone in the app.')
                confirm_void = st.checkbox('I confirm this invoice should be voided', key='void_' + token)
                if st.button('Void invoice', disabled=not confirm_void):
                    run_write(lambda: db.void_expense(engine, record['id'], record['version']), 'Invoice voided.')
    if action == 'Assign categories':
        by_id={r['id']:r for r in visible_rows}
        st.caption('Filter by supplier or description above, then select invoices to categorise together. Amounts and dates stay as recorded.')
        select_all=st.checkbox(f'Select all {len(by_id)} shown invoices',value=False)
        chosen=list(by_id) if select_all else st.multiselect('Invoices to categorise',list(by_id),format_func=lambda key:
            f"{by_id[key].get('display_id',key)} · {by_id[key].get('legacy_code') or by_id[key]['invoice_code']} · {by_id[key]['description']}")
        bulk_category=st.selectbox('Assign to',db.CATEGORIES,index=None,placeholder='Choose category')
        if st.button('Apply category',disabled=not chosen or not bulk_category):
            run_write(lambda: db.assign_categories(engine,[by_id[key] for key in chosen],bulk_category),f'Updated {len(chosen)} invoices.')
    st.subheader(period_label + ' invoices')
    ui.review_notice(summary)
    st.caption(f'Showing {len(visible_rows):,} of {len(expense_rows):,} invoices')
    show_money_table(expenses_frame(visible_rows), ['Amount'])
    st.metric('Monthly expenses', db.rm(summary['total_expenses']))

elif page == 'Monthly summary':
    st.title('Summary · ' + period_label)
    metrics(summary)
    st.caption('Debit = recorded sales. Credit = expenses assigned to this reporting month, following your workbook layout.')
    ui.summary_table(summary)
    ui.review_notice(summary)
    st.caption('This is the sales-minus-expenses result from your workbook, based on the records entered.')

elif page == 'Export records':
    st.title('Export records')
    st.caption('Monthly CSV reports open in Excel. Full records include every month and voided invoices.')
    def csv_bytes(frame):
        frame = frame.copy()
        for col in frame.select_dtypes(include='object'):
            frame[col] = frame[col].map(lambda v: "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v)
        return frame.to_csv(index=False).encode('utf-8-sig')
    with st.expander('Workbook import report'):
        from history_import import load_bundle
        bundle=load_bundle()
        st.write(f"{len(bundle['sales']):,} recorded sales days · {len(bundle['expenses']):,} invoices · {len(bundle['summaries'])} months")
        st.caption('Detailed records determine the live totals. Old summary amounts are retained for reconciliation. Blank sales dates are not assumed to be zero. Older summaries sometimes combine E-Wallet with a sales column; the import keeps the original three payment columns separate.')
        rows=[]
        for r in bundle['summaries']:
            rows.append({'Month':r['reporting_month'][:7],'Detailed sales':r['detail_sales_sen']/100,
                'Workbook summary sales':sum(r['debit'].values())/100 if r['debit'] else None,
                'Detailed expenses':r['detail_expenses_sen']/100,
                'Workbook summary expenses':sum(r['credit'].values())/100 if r['credit'] else None})
        st.dataframe(pd.DataFrame(rows),hide_index=True,width='stretch')
        st.download_button('Download source issues CSV',csv_bytes(pd.DataFrame(bundle['issues'])),'workbook_import_issues.csv','text/csv')
        st.download_button('Download original monthly allocations',json.dumps(bundle['summaries'],ensure_ascii=False,indent=2).encode(),
                           'original_monthly_allocations.json','application/json')
        with engine.connect() as conn:
            from sqlalchemy import select
            reports=[json.loads(r) for r in conn.execute(select(db.import_batches.c.report_json)).scalars()]
        for report in reports:
            st.write(f"Imported here: {report['sales_added']} sales days and {report['expenses_added']} expenses. Existing matching entries were left in place.")
            if report['conflicts']:
                st.warning(f"{len(report['conflicts'])} sales-date conflicts: your existing records were kept. Review the workbook values before making any correction.")
                st.download_button('Download sales conflicts',json.dumps(report['conflicts'],indent=2).encode(),'sales_import_conflicts.json','application/json')
    for label,frame,name in [('Daily sales',sales_frame(sale_rows),'sales'),
                              ('Expenses',expenses_frame(expense_rows),'expenses'),
                              ('Monthly summary',summary_frame(summary),'summary')]:
        st.download_button(label+' CSV',csv_bytes(frame),f'{year}-{month:02d}_{name}.csv','text/csv')
    if st.button('Prepare full records export'):
        st.session_state['full_export'] = json.dumps({'schema_version':2,
            'exported_at':db.timestamp(), 'currency':'MYR', 'money_unit':'sen',
            'tables':db.all_records(engine)},default=str,indent=2).encode()
    if 'full_export' in st.session_state:
        st.download_button('Download full records (JSON)', st.session_state['full_export'],
                           'restaurant_records.json','application/json')
        st.caption('Snapshot from the last time you selected Prepare. Keep it private. Database restore is an administrator task; see README.md.')
