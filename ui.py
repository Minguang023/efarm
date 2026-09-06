"""Shared presentation: native Streamlit controls with forest and warm-paper colours."""
import html
import altair as alt
import pandas as pd
import streamlit as st
import db

PALETTE=['#1c6652','#83aa80','#d2a35e','#507f89','#b8b9ac']

def style():
    st.html('''<style>
    .stApp {background:#f6f5ef;color:#243b32}
    .block-container {max-width:1400px;padding-top:2rem;padding-bottom:3rem}
    h1,h2,h3 {letter-spacing:-.035em;color:#193f32}
    h1 {font-weight:750!important;font-size:2.35rem!important}
    [data-testid="stSidebar"] {background:#183f32}
    [data-testid="stSidebar"] h1,[data-testid="stSidebar"] p,[data-testid="stSidebar"] label,
    [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {color:#f3f1df!important}
    [data-testid="stSidebar"] [data-testid="stRadio"] label {padding:.38rem .1rem}
    [data-testid="stMetric"] {background:#fffdf7;border:1px solid #dedfd2;border-radius:16px;padding:1.1rem 1.2rem;min-height:130px}
    [data-testid="stMetricValue"] {color:#1d5945;font-size:clamp(1.45rem,2.3vw,2.25rem)}
    [data-testid="stMetricLabel"] {color:#657165}
    [data-testid="stVerticalBlockBorderWrapper"] {border-radius:16px}
    button[kind="primary"] {border-radius:9px;background:#205e47;border-color:#205e47}
    [data-testid="stDataFrame"] {border-radius:12px;overflow:hidden}
    .eyebrow {font-size:.78rem;letter-spacing:.16em;color:#77856d;font-weight:650;margin-bottom:.35rem}
    .summary-table {width:100%;border-collapse:collapse;background:#fffdf7;border-radius:14px;overflow:hidden;font-size:1rem}
    .summary-table td,.summary-table th {padding:15px 20px;border-bottom:1px solid #e6e6dc;text-align:left}
    .summary-table th {background:#e9eddf;color:#53634f;font-size:.88rem}
    .summary-table .num {text-align:right;font-variant-numeric:tabular-nums}
    .summary-table tr:last-child {background:#e6eddf;font-weight:700}
    @media(max-width:700px){.block-container{padding:1rem}.summary-table td,.summary-table th{padding:10px;font-size:.88rem}}
    </style>''')

def summary_table(summary):
    rows=[]
    for label,value in summary['debit'].items(): rows.append((label,f'{value/100:,.2f}','—'))
    for label,value in summary['credit'].items(): rows.append((label,'—',f'{value/100:,.2f}'))
    rows.append(('TOTAL',f'{summary["total_sales"]/100:,.2f}',f'{summary["total_expenses"]/100:,.2f}'))
    body=''.join('<tr>'+''.join(f'<td class="{"num" if i else ""}">{html.escape(v)}</td>' for i,v in enumerate(row))+'</tr>' for row in rows)
    st.html('<table class="summary-table"><thead><tr><th>Description</th><th class="num">Debit (RM)</th><th class="num">Credit (RM)</th></tr></thead><tbody>'+body+'</tbody></table>')

def review_notice(summary):
    if summary['missing_amounts']:
        st.warning(f"{summary['missing_amounts']} invoice(s) have no amount. Totals include known amounts only; review them in Expenses.")
    if summary['unclassified']:
        st.caption(f"{summary['unclassified']} historical invoices need a category. Their recorded amounts are included under Uncategorised, so they still count towards expenses and profit/loss.")

def dashboard(engine,period,sale_rows,expense_rows,summary,sales_frame,expenses_frame):
    top,actions=st.columns([3,2])
    with top:
        st.html('<div class="eyebrow">E FARM · 森苑饭庄</div>')
        st.title('Business overview')
        st.caption(period.strftime('%B %Y')+' · Malaysian ringgit')
    with actions:
        a,b=st.columns(2)
        a.button('＋ Record sales',width='stretch',on_click=lambda:st.session_state.update(workspace='Daily sales'))
        b.button('＋ Add expense',width='stretch',on_click=lambda:st.session_state.update(workspace='Expenses'))
    previous=(period.replace(day=1)-pd.Timedelta(days=1)).date() if isinstance(period,pd.Timestamp) else period-pd.Timedelta(days=1)
    ps,pe=db.monthly_records(engine,previous.year,previous.month);prior=db.summarize(ps,pe)
    cards=st.columns(4)
    for col,label,key in zip(cards,['Total sales','Total expenses','Profit / loss'],['total_sales','total_expenses','profit']):
        delta=db.rm(summary[key]-prior[key])+' vs previous month' if ps or pe else None
        col.metric(label,db.rm(summary[key]),delta=delta,delta_color='inverse' if key=='total_expenses' else 'normal')
    margin=(summary['profit']/summary['total_sales']*100) if summary['total_sales'] else None
    cards[3].metric('Profit margin',f'{margin:.1f}%' if margin is not None else '—')
    st.caption(f'{len(sale_rows)} sales days · {len(expense_rows):,} invoices · Comparisons use recorded totals; months may have different coverage.')
    review_notice(summary)
    left,right=st.columns([2,1])
    with left,st.container(border=True):
        st.subheader('Daily sales rhythm')
        if sale_rows:
            frame=sales_frame(sale_rows)
            base=alt.Chart(frame).encode(x=alt.X('Date:T',title=None,axis=alt.Axis(format='%d %b')),
                y=alt.Y('Total daily sales:Q',title='RM',axis=alt.Axis(format=',.0f')),
                tooltip=[alt.Tooltip('Date:T',format='%d %b %Y'),alt.Tooltip('Total daily sales:Q',format=',.2f',title='Sales (RM)')])
            chart=base.mark_area(color='#adc7a8',opacity=.35)+base.mark_line(color='#205e47',strokeWidth=3,point=True)
            st.altair_chart(chart.properties(height=280),width='stretch')
            st.caption('Average per recorded day: '+db.rm(round(summary['total_sales']/len(sale_rows))))
        else: st.info('No sales yet. Record your first day using the button above.')
    with right,st.container(border=True):
        st.subheader('Payment mix')
        if summary['total_sales']:
            frame=pd.DataFrame([{'Payment':k,'RM':v/100} for k,v in summary['debit'].items()])
            chart=alt.Chart(frame).mark_arc(innerRadius=65,outerRadius=105).encode(theta='RM:Q',
                color=alt.Color('Payment:N',scale=alt.Scale(range=PALETTE),legend=alt.Legend(orient='bottom',title=None)),
                tooltip=['Payment:N',alt.Tooltip('RM:Q',format=',.2f')])
            st.altair_chart(chart.properties(height=235),width='stretch')
            for label,value in summary['debit'].items(): st.caption(f'{label} · {db.rm(value)}')
        else: st.info('Payment breakdown appears when sales are recorded.')
    left,right=st.columns([2,1])
    with left,st.container(border=True):
        st.subheader(f'{period.year} at a glance')
        data=db.year_trend(engine,period.year)
        if data:
            frame=pd.DataFrame([{'Month':r['month'],'Type':label,'RM':r[key]/100} for r in data
                for label,key in [('Sales','total_sales'),('Expenses','total_expenses')]])
            chart=alt.Chart(frame).mark_bar(cornerRadiusTopLeft=3,cornerRadiusTopRight=3).encode(
                x=alt.X('yearmonth(Month):O',title=None,axis=alt.Axis(format='%b',labelAngle=0)),
                xOffset='Type:N',y=alt.Y('RM:Q',title='RM',axis=alt.Axis(format='~s')),
                color=alt.Color('Type:N',scale=alt.Scale(domain=['Sales','Expenses'],range=['#205e47','#c6a267']),legend=alt.Legend(orient='bottom',title=None)),
                tooltip=[alt.Tooltip('Month:T',format='%b %Y'),'Type:N',alt.Tooltip('RM:Q',format=',.2f')])
            st.altair_chart(chart.properties(height=260),width='stretch')
        else: st.info('Your monthly comparisons will appear here.')
    with right,st.container(border=True):
        st.subheader('Where money went')
        for label,value in sorted(summary['credit'].items(),key=lambda item:item[1],reverse=True):
            st.write(f'**{label}** · {db.rm(value)}')
            st.progress(max(0.,min(1.,value/summary['total_expenses'])) if summary['total_expenses'] else 0.)
    with st.container(border=True):
        st.subheader('Recent invoices')
        if expense_rows:
            recent=sorted(expense_rows,key=lambda r:r['invoice_date'] or period,reverse=True)[:8]
            frame=expenses_frame(recent)
            st.dataframe(frame[['Invoice date','Invoice code','Description','Category','Amount']],hide_index=True,width='stretch',
                column_config={'Amount':st.column_config.NumberColumn('Amount (RM)',format='%.2f')})
        else: st.caption('No invoices in this month.')
