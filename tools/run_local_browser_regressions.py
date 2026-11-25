"""Real Chromium DOM events against a synthetic native-save model, NOT Frappe acceptance.
Requires the preinstalled agent-browser CLI. No remote site or customer data is used.
"""
from pathlib import Path
import json
import subprocess
import sys
import time
import tempfile
import shutil
import threading
from functools import partial
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT=Path(__file__).resolve().parents[1]
temporary=tempfile.TemporaryDirectory(prefix='flexi-local-browser-')
webroot=Path(temporary.name)
(webroot/'loan-application-new/new').mkdir(parents=True)
shutil.copyfile(ROOT/'comfort_homes/tests/webform_submit_harness.html',webroot/'loan-application-new/new/index.html')
shutil.copyfile(ROOT/'comfort_homes/public/js/hp_webform_submit.js',webroot/'hp_webform_submit.js')
server=ThreadingHTTPServer(('127.0.0.1',0),partial(SimpleHTTPRequestHandler,directory=str(webroot)))
threading.Thread(target=server.serve_forever,daemon=True).start()
URL=f'http://127.0.0.1:{server.server_port}/loan-application-new/new/'
results=[]
def command(*args):
    return subprocess.run(['agent-browser',*args],check=True,capture_output=True,text=True).stdout.strip()
def js(code):return command('eval',code)
def fresh(scenario='success',delay=30):
    command('open',URL)
    js('test.scenario='+json.dumps(scenario)+';test.delay='+str(delay))
def settle():
    command('wait','--fn','["saved","rejected","uncertain"].includes(flexiSubmit.phase())')
def check(name,expect):
    # Throw inside the actual browser, so a successful CLI action alone cannot pass.
    js('if(!('+expect+')) throw new Error('+json.dumps(name)+');')
    results.append(dict(test=name,passed=True,observed=js('JSON.stringify({saves:test.saves,successes:test.successes,lookups:test.lookups,phase:flexiSubmit.phase(),value:document.querySelector("input").value})')))
try:
    for owner in ('frm','df'):
        fresh()
        js("const source=[{name:'keep',product_description:'Gift',quantity:1,unit_price:0},{name:'blank',quantity:1,unit_price:0}];const df={fieldname:'custom_flexi_purchase_items',data:source};const grid={df,refresh(){},get_data(){return this.frm?this.frm.doc[df.fieldname]:this.df.data}};" + ("grid.frm={doc:{custom_flexi_purchase_items:source}};" if owner=='frm' else "") + "window.fake={fields_dict:{custom_flexi_purchase_items:{df,grid}},get_value:()=>[]};flexiSubmit.cleanup(fake);")
        check('zero-price row retained/blank removed '+owner,"fake.fields_dict.custom_flexi_purchase_items.grid.get_data().length===1 && fake.fields_dict.custom_flexi_purchase_items.grid.get_data()[0].name==='keep'")
    fresh();command('snapshot','-i');command('find','role','button','click','--name','Submit');settle();check('first physical click','test.saves===1 && test.successes===1 && flexiSubmit.phase()==="saved"')
    fresh();js('document.querySelector("input").focus()');command('press','Enter');settle();check('keyboard Enter','test.saves===1 && test.successes===1')
    fresh(delay=200);js('document.querySelector("button").click();document.querySelector("button").click();document.querySelector("form").requestSubmit()');settle();check('double click plus concurrent form submit','test.saves===1 && test.successes===1 && test.lookups===1')
    for scenario in ('duplicate','invalid','lookup-error','early'):
        fresh(scenario);js('document.querySelector("button").click()');settle();check(scenario+' retains entries/no save','test.saves===0 && flexiSubmit.phase()==="rejected" && document.querySelector("input").value==="Synthetic test"')
        js('test.scenario="success";document.querySelector("button").click()');settle();check(scenario+' successful explicit retry','test.saves===1 && test.successes===1')
    fresh('server-error');js('document.querySelector("button").click()');settle();check('explicit server rejection retains entry','test.saves===1 && test.successes===0 && flexiSubmit.phase()==="rejected"')
    js('test.scenario="success";document.querySelector("button").click()');settle();check('server rejection retry','test.saves===2 && test.successes===1')
    fresh('lost');js('document.querySelector("button").click()');settle();js('frappe.web_form.save()');check('lost response blocks blind retry','test.saves===1 && test.successes===0 && flexiSubmit.phase()==="uncertain"')
    fresh(delay=200);js('document.querySelector("button").click();setTimeout(()=>{document.querySelector("input").value="Changed input";document.querySelector("input").dispatchEvent(new Event("input",{bubbles:true}));},20)');settle();check('input changes during lookup invalidate approval','test.saves===0 && flexiSubmit.phase()==="rejected" && document.querySelector("input").value==="Changed input"')
finally:
    command('close')
    server.shutdown(); server.server_close(); temporary.cleanup()
output=dict(scope='Real local browser events; mocked native-save/HTTP lifecycle, no Frappe site',results=results,total_passed=len(results))
print(json.dumps(output,indent=2))
