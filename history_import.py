"""Transactional, repeat-safe import. Existing user records always win."""
import json
from datetime import date
from pathlib import Path
from sqlalchemy import insert, select
import db

def load_bundle():
    return json.loads((Path(__file__).parent/'history/records.json').read_text(encoding='utf-8'))

def import_history(engine,bundle=None):
    bundle=bundle or load_bundle()
    batch_id=bundle['source_sha256']
    with engine.begin() as conn:
        if engine.dialect.name=='postgresql':
            from sqlalchemy import text
            conn.execute(text('SELECT pg_advisory_xact_lock(5826012026)'))
        elif engine.dialect.name=='sqlite':
            # Serialize startup imports across processes, not only browser sessions.
            conn.exec_driver_sql('BEGIN IMMEDIATE')
        done=conn.execute(select(db.import_batches).where(db.import_batches.c.id==batch_id)).mappings().first()
        if done: return json.loads(done['report_json'])
        report=dict(sales_added=0,expenses_added=0,conflicts=[],duplicate_expenses_skipped=0,source=bundle['source_name'])
        existing={r['sale_date']:dict(r) for r in conn.execute(select(db.sales)).mappings()}
        for item in bundle['sales']:
            row={k:v for k,v in item.items() if k!='source'};row['sale_date']=date.fromisoformat(row['sale_date'])
            if row['sale_date'] in existing:
                if any(existing[row['sale_date']][k]!=row[k] for k in ('sales_1_sen','sales_2_sen','e_wallet_sen')):
                    report['conflicts'].append(dict(date=str(row['sale_date']),reason='Existing sales kept; workbook values available in import bundle',workbook=item))
                continue
            conn.execute(insert(db.sales).values(**row,version=1,updated_at=db.timestamp()))
            existing[row['sale_date']]=row;report['sales_added']+=1
        ids=set(conn.execute(select(db.historical_expenses.c.id)).scalars())
        def signature(r):
            return tuple(r[k] for k in ('invoice_date','reporting_month','invoice_code','description','amount_sen'))
        manual={signature(r) for r in conn.execute(select(db.expenses)).mappings()}
        for item in bundle['expenses']:
            row=dict(item)
            row['invoice_date']=date.fromisoformat(row['invoice_date']) if row['invoice_date'] else None
            row['reporting_month']=date.fromisoformat(row['reporting_month'])
            if row['id'] in ids: continue
            if signature(row) in manual:
                report['duplicate_expenses_skipped']+=1;continue
            conn.execute(insert(db.historical_expenses).values(**row,version=1,voided=False,updated_at=db.timestamp()))
            ids.add(row['id']);report['expenses_added']+=1
        for item in bundle['summaries']:
            month=date.fromisoformat(item['reporting_month'])
            if not conn.execute(select(db.workbook_summaries.c.reporting_month).where(db.workbook_summaries.c.reporting_month==month)).first():
                conn.execute(insert(db.workbook_summaries).values(reporting_month=month,payload_json=json.dumps(item)))
        conn.execute(insert(db.import_batches).values(id=batch_id,report_json=json.dumps(report),imported_at=db.timestamp()))
    return report
