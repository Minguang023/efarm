from pathlib import Path
from streamlit.testing.v1 import AppTest

APP = Path(__file__).parents[1]/'app.py'

def test_setup_guard(monkeypatch):
    monkeypatch.setenv('AUTO_IMPORT_HISTORY','false')
    monkeypatch.setenv('LOCAL_DEMO','false')
    monkeypatch.setenv('APP_PASSWORD','')
    monkeypatch.setenv('DATABASE_URL','')
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert at.title[0].value == 'E FARM · Setup'

def test_pages_and_sales_entry(monkeypatch,tmp_path):
    monkeypatch.setenv('AUTO_IMPORT_HISTORY','false')
    monkeypatch.setenv('LOCAL_DEMO','true')
    monkeypatch.setenv('APP_PASSWORD','')
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "ui.db"}')
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    at.sidebar.radio[0].set_value('Daily sales').run()
    for label,value in [('Sales 1 (RM)',100.1),('Sales 2 (RM)',200.2),('E-Wallet (RM)',50.3)]:
        next(x for x in at.number_input if x.label==label).set_value(value)
    at.run()
    assert next(x for x in at.metric if x.label=='Total daily sales').value == 'RM350.60'
    next(x for x in at.button if x.label=='Save daily sales').click().run()
    assert not at.exception
    assert any('RM350.60' in x.value for x in at.success)
    for page in ['Expenses','Monthly summary','Export records','Dashboard']:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, page

def test_password_gate(monkeypatch,tmp_path):
    monkeypatch.setenv('AUTO_IMPORT_HISTORY','false')
    monkeypatch.setenv('LOCAL_DEMO','true')
    monkeypatch.setenv('APP_PASSWORD','test-long-password')
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "auth.db"}')
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert len(at.sidebar.radio) == 0
    at.text_input[0].set_value('wrong')
    at.button[0].click().run()
    assert at.error
    at.text_input[0].set_value('test-long-password')
    at.button[0].click().run()
    assert not at.exception
    assert at.sidebar.radio[0].value == 'Dashboard'

def test_invoice_lifecycle(monkeypatch,tmp_path):
    monkeypatch.setenv('AUTO_IMPORT_HISTORY','false')
    monkeypatch.setenv('LOCAL_DEMO','true')
    monkeypatch.setenv('APP_PASSWORD','')
    monkeypatch.setenv('DATABASE_URL',f'sqlite:///{tmp_path / "invoice-ui.db"}')
    at = AppTest.from_file(str(APP)).run()
    at.sidebar.radio[0].set_value('Expenses').run()
    next(x for x in at.text_input if x.label=='Invoice code').set_value('001-ABC')
    next(x for x in at.text_input if x.label=='Description').set_value('Fresh ingredients')
    next(x for x in at.selectbox if x.label=='Category').set_value('Stock')
    next(x for x in at.number_input if x.label=='Amount (RM)').set_value(123.45)
    next(x for x in at.button if x.label=='Save invoice').click().run()
    assert not at.exception
    assert next(x for x in at.metric if x.label=='Monthly expenses').value == 'RM123.45'
    next(x for x in at.radio if x.label=='Action').set_value('Edit / void invoice').run()
    next(x for x in at.number_input if x.label=='Amount (RM)').set_value(100.25)
    next(x for x in at.button if x.label=='Save changes').click().run()
    assert not at.exception
    assert next(x for x in at.metric if x.label=='Monthly expenses').value == 'RM100.25'
    next(x for x in at.checkbox if x.label=='I confirm this invoice should be voided').check().run()
    next(x for x in at.button if x.label=='Void invoice').click().run()
    assert not at.exception
    assert next(x for x in at.metric if x.label=='Monthly expenses').value == 'RM0.00'
