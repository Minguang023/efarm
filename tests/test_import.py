from datetime import date
import pytest
from streamlit.testing.v1 import AppTest
from pathlib import Path
import db
from history_import import import_history,load_bundle

@pytest.fixture
def engine(tmp_path):
    e=db.connect(f'sqlite:///{tmp_path/"import.db"}')
    yield e
    e.dispose()

def test_import_repeat_safe_preserves_edits(engine):
    bundle=load_bundle()
    first=import_history(engine,bundle)
    assert first['sales_added']==598
    assert first['expenses_added']==4683
    assert import_history(engine,bundle)==first
    assert len(db.all_records(engine)['historical_expenses'])==4683
    db.save_sale(engine,date(2026,7,1),[1,2,3],expected_version=1)
    import_history(engine,bundle)
    assert db.get_sale(engine,date(2026,7,1))['sales_1_sen']==100

def test_existing_data_wins(engine):
    db.save_sale(engine,date(2026,7,1),[1,2,3])
    result=import_history(engine)
    assert result['sales_added']==597
    assert len(result['conflicts'])==1
    assert db.get_sale(engine,date(2026,7,1))['sales_1_sen']==100

def test_every_month_matches_detail_totals(engine):
    b=load_bundle();import_history(engine,b)
    for month in b['summaries']:
        d=date.fromisoformat(month['reporting_month'])
        summary=db.summarize(*db.monthly_records(engine,d.year,d.month))
        assert summary['total_sales']==month['detail_sales_sen']
        assert summary['total_expenses']==month['detail_expenses_sen']
    # The sheet is January 2026, although the underlying Excel dates use 2025.
    assert len(db.monthly_records(engine,2026,1)[0])==31
    assert len(b['blank_sales'])==10
    assert sum(r['amount_sen'] is None for r in b['expenses'])==1
    assert sum(r['invoice_date'] is None for r in b['expenses'])==4

def test_bulk_category_preserves_money_and_source(engine):
    import_history(engine)
    rows=db.monthly_records(engine,2026,7)[1]
    chosen=[r for r in rows if r['category'] is None][:3]
    before=db.summarize([],rows)['total_expenses']
    db.assign_categories(engine,chosen,'Stock')
    after=db.monthly_records(engine,2026,7)[1]
    assert db.summarize([],after)['total_expenses']==before
    for row in chosen:
        saved=db.get_expense(engine,row['id'])
        assert saved['category']=='Stock'
        assert saved['source_json']==row['source_json']
    with pytest.raises(db.Conflict): db.assign_categories(engine,chosen,'Bills')

def test_populated_dashboard_and_historical_edit(monkeypatch,tmp_path):
    monkeypatch.setenv('AUTO_IMPORT_HISTORY','true')
    monkeypatch.setenv('LOCAL_DEMO','true')
    monkeypatch.setenv('APP_PASSWORD','')
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path/"populated.db"}')
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    assert not at.exception
    assert next(x for x in at.metric if x.label=='Total sales').value=='RM275,862.00'
    assert 'July reference' not in at.sidebar.radio[0].options
    at.sidebar.radio[0].set_value('Expenses').run()
    next(x for x in at.radio if x.label=='Action').set_value('Edit / void invoice').run()
    assert not at.exception
    at.sidebar.radio[0].set_value('Monthly summary').run()
    assert not at.exception
    assert all('Profit / loss (Debit' not in x.value for x in at.markdown)
