"""Execute the pure browser preview in Node; not a browser UI/site test."""
import json
from pathlib import Path
import random
import shutil
import subprocess

import pytest

from comfort_homes.hp_lending.origination_math import assessment_values, contract_values, profile_values

ROOT = Path(__file__).resolve().parents[1]


def node_call(action, data):
    if not shutil.which('node'):
        pytest.skip('Node is required to execute browser preview regressions')
    program = '''const fs=require('fs'),vm=require('vm'); global.window={};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const result=input.action==='round'?input.data.map(x=>window.comfort_hp_math.round(x[0],x[1])):input.data.map(x=>window.comfort_hp_math.evaluate(x,input.action==='percent'?'percent':'amount'));
process.stdout.write(JSON.stringify(result));'''
    run = subprocess.run(['node','-e',program,str(ROOT/'public/js/hp_origination_math.js')],
                         input=json.dumps({'action':action,'data':data}),text=True,capture_output=True,check=True)
    return json.loads(run.stdout)


def test_binary_rounding_matches_python_for_ties_boundaries_and_random_inputs():
    rng=random.Random(20261117)
    values=[0.125,0.375,2.675,1.005,16.975,158.6156,6.5,-0.125,-2.675,1e308,5e-324]
    values += [rng.uniform(-100000,100000) for _ in range(300)]
    vectors=[(value,precision) for value in values for precision in [0,2]]
    assert node_call('round',vectors)==[round(v,p) for v,p in vectors]


def test_web_and_desk_shared_engine_matches_saved_origination_policy():
    docs=[]
    for frequency in ['Monthly','Weekly','Fortnightly','Bi-monthly']:
        for months in [3,6,12,18]:
            for rate in [12,19,22,25,27]:
                docs.append({'custom_flexi_purchase_items':[{'quantity':1,'unit_price':849},{'quantity':1,'unit_price':0}],
                    'custom_flexi_deposit_amount':170,'custom_flexi_documentation_fee':25,'custom_flexi_admin_fee_rate':2.5,
                    'custom_flexi_term_months':months,'custom_flexi_interest_rate':rate,'custom_flexi_selected_dsr':35,
                    'custom_flexi_payment_period':'Fortnightly','custom_flexi_income_entries':[
                        {'income_type':'Net Salary / Wages','frequency':'Monthly','amount':1000.01},
                        {'income_type':'Other Income','frequency':frequency,'amount':33.33},
                        {'income_type':'Salary Deductions','frequency':'Weekly','amount':-0.01}],
                    'custom_flexi_living_expenses':[{'expense_amount':20.01,'frequency_per_month':4}],
                    'custom_flexi_finance_obligations':[{'frequency':'Weekly','amount':0.01}]})
    # Node mutates only its JSON copies, just as the real frontend updates child totals.
    actual=node_call('evaluate',docs)
    for doc,fields in zip(docs,actual):
        profile=profile_values(doc['custom_flexi_income_entries'],doc['custom_flexi_living_expenses'],doc['custom_flexi_finance_obligations'])
        contract=contract_values(doc,doc['custom_flexi_purchase_items'])
        expected={**profile['fields'],**contract['fields'],**assessment_values(profile,contract,35,'Fortnightly')}
        for key in set(expected)&set(fields):
            assert fields[key]==expected[key],(key,doc['custom_flexi_term_months'],doc['custom_flexi_interest_rate'])
    assert len(actual)==80


def test_managed_and_primary_preview_delegate_to_shared_engine():
    web=json.loads((ROOT/'fixtures/web_form.json').read_text())[0]['client_script']
    desk=next(x['script'] for x in json.loads((ROOT/'fixtures/client_script.json').read_text()) if x['name']=='Flexi Loan Application Calculations')
    assert 'window.comfort_hp_math.evaluate' in web
    assert 'window.comfort_hp_math.evaluate' in desk
    assert 'if (frm.doctype !== "Loan Application" || frm.doc.docstatus !== 0) return;' in desk


def test_javascript_sources_parse():
    if not shutil.which('node'): pytest.skip('Node required')
    for path in (ROOT/'public/js').glob('*.js'):
        subprocess.run(['node','--check',str(path)],check=True,capture_output=True,text=True)
    scripts=[r.get('script') for r in json.loads((ROOT/'fixtures/client_script.json').read_text()) if r.get('enabled')]
    scripts += [json.loads((ROOT/'fixtures/web_form.json').read_text())[0]['client_script']]
    for script in scripts:
        subprocess.run(['node','--check'],input=script,text=True,check=True,capture_output=True)


def test_percentage_input_does_not_feed_its_rounded_actual_percentage_back_into_itself():
    doc={'custom_flexi_purchase_items':[{'quantity':1,'unit_price':849}],
         'custom_flexi_deposit_percent':10,'custom_flexi_deposit_amount':0,
         'custom_flexi_term_months':12,'custom_flexi_interest_rate':22}
    fields=node_call('percent',[doc])[0]
    assert fields['custom_flexi_deposit_amount']==85
    assert fields['custom_flexi_deposit_percent']==10
    doc.update(fields)
    doc['custom_flexi_purchase_items']=[{'quantity':1,'unit_price':1000}]
    assert node_call('percent',[doc])[0]['custom_flexi_deposit_amount']==100
