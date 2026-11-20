"""Execute Desk action code under Node stubs; not a real browser/session test."""
import json
from pathlib import Path
import subprocess


def test_desk_evidence_actions_use_private_native_uploader_and_no_contract_signing():
    asset=Path(__file__).resolve().parents[1]/'public/js/document_evidence.js'
    script=r'''
const vm = require('node:vm');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const source=fs.readFileSync(process.argv[1], 'utf8');
const handlers={}; let registrations=0, uploaded, api=[], messages=[];
const frappe={session:{user:'officer@example.invalid'}, user_roles:['Loan Officer'],
    ui:{form:{on:(type, h)=>{handlers[type]=h;registrations++;}},FileUploader:function(opts){uploaded=opts;}},
    call:opts=>{api.push(opts);return Promise.resolve({message:{file_url:'/private/files/a.pdf'}});},
    show_alert:()=>{},msgprint:msg=>messages.push(msg),
    confirm:(text,yes)=>yes(),
};
const context={window:{},frappe,__:x=>x,console}; vm.createContext(context);
vm.runInContext(source,context);vm.runInContext(source,context);
assert.equal(registrations,2,'Shared asset must register each DocType only once');
function form(type,state){return {doctype:type,doc:{name:'APP',docstatus:0,workflow_state:state,custom_document_collection_complete:'Yes'},
    is_new:()=>false,is_dirty:()=>false,reload_doc:()=>Promise.resolve(),buttons:{},
    add_custom_button(label,action){this.buttons[label]=action;}};}
const app=form('Loan Application','Contract Signing');handlers['Loan Application'].refresh(app);
assert.equal(app.buttons['Record Wet-signed Scan'],undefined);assert.ok(app.buttons['Record Combined Signed Pack']);assert.equal(app.buttons['Verify Uploaded Contract Pack'],undefined);
app.buttons['Record Printed Assessment PDF']();
assert.equal(uploaded.make_attachments_public,false);assert.equal(uploaded.allow_toggle_private,false);
assert.equal(uploaded.allow_web_link,false);assert.equal(uploaded.allow_google_drive,false);
assert.equal(uploaded.allow_multiple,false);assert.equal(uploaded.disable_file_browser,true);
uploaded.on_success({is_private:0,file_url:'/files/not-private.pdf'});assert.equal(api.length,0);
uploaded.on_success({is_private:1,file_url:'/private/files/a.pdf'});
assert.equal(api[0].args.confirm_current,1);assert.equal(api[0].type,'POST');
const profile=form('Co-Applicant','Draft');handlers['Co-Applicant'].refresh(profile);
assert.deepEqual(Object.keys(profile.buttons),['Generate Individual CIRA PDF']);
profile.buttons['Generate Individual CIRA PDF']();assert.equal(api[1].args.doctype,'Co-Applicant');
profile.is_dirty=()=>true;profile.buttons['Generate Individual CIRA PDF']();assert.equal(api.length,2);
frappe.user_roles=['Finance Manager'];const manager=form('Loan Application','Contract Signing');
handlers['Loan Application'].refresh(manager);assert.ok(manager.buttons['Verify Uploaded Contract Pack']);
manager.buttons['Verify Uploaded Contract Pack']();assert.ok(api[2].method.endsWith('.verify_contract_pack'));
assert.ok(api.every(x=>!x.method.includes('agreement_signing')));
console.log(JSON.stringify({registered:registrations,private_upload:true,digital_contract_calls:0}));
'''
    result=subprocess.run(['node','-e',script,str(asset)],capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)=={'registered':2,'private_upload':True,'digital_contract_calls':0}
