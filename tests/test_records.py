import json
from datetime import date
from pathlib import Path

import pytest
import db

@pytest.fixture
def engine(tmp_path):
    engine = db.connect(f'sqlite:///{tmp_path / "test.db"}')
    yield engine
    engine.dispose()

def test_july_reconciliation(engine):
    from history_import import import_history, load_bundle
    bundle=load_bundle()
    import_history(engine,bundle)
    totals=db.summarize(*db.monthly_records(engine,2026,7))
    assert totals['debit']=={'Sales 1':4348800,'Sales 2':23401500,'E-Wallet':5726600}
    assert totals['total_sales']==33476900
    assert totals['total_expenses']==32952115
    assert totals['profit']==524785
    assert totals['missing_amounts']==1


def test_money_validation():
    assert db.to_sen('0.10') + db.to_sen('0.20') == 30
    assert db.rm(-524785) == 'RM-5,247.85'
    for amount in ['-1','1.001','nan','inf','abc','100000000']:
        with pytest.raises(ValueError): db.to_sen(amount)

def test_dates_and_reporting_month(engine):
    db.save_sale(engine,date(2026,12,31),[10,20,30])
    db.save_sale(engine,date(2027,1,1),[1,2,3])
    db.add_expense(engine,date(2026,11,20),date(2026,12,25),'0001-AB','Old invoice','Stock','7.15')
    s,e = db.monthly_records(engine,2026,12)
    assert len(s) == len(e) == 1
    assert e[0]['reporting_month'] == date(2026,12,1)
    assert e[0]['invoice_code'] == '0001-AB'
    assert db.summarize(s,e)['profit'] == 5285
    assert db.month_bounds(2028,2) == (date(2028,2,1),date(2028,3,1))

def test_duplicate_and_stale_sales(engine):
    day = date(2026,7,1)
    db.save_sale(engine,day,[1,2,3])
    with pytest.raises(db.Conflict): db.save_sale(engine,day,[10,20,30])
    db.save_sale(engine,day,[10,20,30],expected_version=1)
    with pytest.raises(db.Conflict): db.save_sale(engine,day,[100,200,300],expected_version=1)
    assert db.get_sale(engine,day)['sales_1_sen'] == 1000

def test_expense_edit_void_and_submission_id(engine):
    values = dict(invoice_date=date(2026,7,1), reporting_month=date(2026,7,1),
                  invoice_code='INV-A/007',description='Electricity',category='Bills',amount=10)
    key = db.add_expense(engine,**values)
    with pytest.raises(db.Conflict): db.add_expense(engine,**values,record_id=key)
    values.update(amount=12.34,reporting_month=date(2026,8,1))
    db.edit_expense(engine,key,1,**values)
    with pytest.raises(db.Conflict): db.edit_expense(engine,key,1,**values)
    assert not db.monthly_records(engine,2026,7)[1]
    assert db.monthly_records(engine,2026,8)[1][0]['amount_sen'] == 1234
    db.void_expense(engine,key,2)
    assert not db.monthly_records(engine,2026,8)[1]
    assert db.all_records(engine)['expenses'][0]['voided']

def test_empty_and_loss(engine):
    assert db.summarize(*db.monthly_records(engine,2026,7))['profit'] == 0
    db.add_expense(engine,date(2026,7,1),date(2026,7,1),'X','Rent','Rental',123.45)
    assert db.summarize(*db.monthly_records(engine,2026,7))['profit'] == -12345

def test_expense_required_fields(engine):
    args = dict(invoice_date=date(2026,7,1),reporting_month=date(2026,7,1),
                invoice_code='I-1',description='Ingredients',category='Stock',amount=1)
    for key,value in [('invoice_code',' '),('description',''),('category','Unknown'),('amount',0)]:
        with pytest.raises(ValueError): db.add_expense(engine,**(args | {key:value}))
