"""Read and drive Internet Explorer_Server (WebBrowser control) pages through their DOM.

Gets IHTMLDocument from the window with WM_HTML_GETOBJECT + ObjectFromLresult, then runs
small ES3 scripts in the page (execScript) so one round trip returns the whole compact view.
Works with old IE document modes (no JSON, no querySelector needed).
"""
from __future__ import annotations

import ctypes
import json

import comtypes
import comtypes.client.dynamic as _dyn
from comtypes import GUID
from comtypes.automation import IDispatch

from . import winlib

IID_IHTMLDocument = GUID("{626FC520-A41E-11CF-A731-00A0C9082637}")
_oleacc = ctypes.oledll.oleacc
_oleacc.ObjectFromLresult.argtypes = [winlib.LRESULT, ctypes.POINTER(GUID), winlib.WPARAM,
                                      ctypes.POINTER(ctypes.POINTER(IDispatch))]
_WM_HTML_GETOBJECT = winlib.user32.RegisterWindowMessageW("WM_HTML_GETOBJECT")
OUT_ATTR = "data-desk-out"


def get_doc(ie_hwnd: int):
    res = ctypes.c_size_t()
    ok = winlib.user32.SendMessageTimeoutW(ie_hwnd, _WM_HTML_GETOBJECT, 0, 0, winlib.SMTO_ABORTIFHUNG, 3000, ctypes.byref(res))
    if not ok or not res.value:
        raise RuntimeError("WM_HTML_GETOBJECT failed (agent not elevated, or page not ready)")
    ptr = ctypes.POINTER(IDispatch)()
    _oleacc.ObjectFromLresult(res.value, ctypes.byref(IID_IHTMLDocument), 0, ctypes.byref(ptr))
    return ptr


def dyn_doc(ie_hwnd: int):
    # comtypes' late-bound wrapper handles the returned sub-objects correctly; raw Invoke on
    # objects returned from Invoke fails with DISP_E_MEMBERNOTFOUND here.
    return _dyn.Dispatch(get_doc(ie_hwnd))


def _inject(doc, code: str) -> None:
    """Fallback: run code by appending an inline <script> element (IE runs it synchronously)."""
    el = doc.createElement("script")
    el.text = code
    host = doc.body or doc.documentElement
    host.appendChild(el)
    host.removeChild(el)


def run(ie_hwnd: int, body: str) -> str:
    """Run `body` (a JS function body that returns a string) inside the page and return its result."""
    doc = dyn_doc(ie_hwnd)
    code = ("(function(){var __r;try{__r=(function(){" + body + "})();}catch(__e){__r='ERR '+(__e.message||__e);}"
            "document.documentElement.setAttribute('" + OUT_ATTR + "',String(__r));})();")
    try:
        doc.parentWindow.execScript(code, "JScript")
    except comtypes.COMError:
        try:
            _inject(doc, code)
        except comtypes.COMError as e:
            raise RuntimeError(f"cannot run script in page: {e}") from None
    de = doc.documentElement
    out = de.getAttribute(OUT_ATTR)
    try:
        de.removeAttribute(OUT_ATTR)
    except comtypes.COMError:
        pass
    return "" if out is None else str(out)


def page_info(ie_hwnd: int) -> tuple[str, str]:
    doc = dyn_doc(ie_hwnd)
    return str(doc.title or ""), str(doc.URL or "")


_COMMON = r"""
function T(s,m){ if(s===null||s===undefined) return ''; s=String(s).replace(/\s+/g,' ').replace(/^ +| +$/g,''); m=m||50; return s.length>m? s.substring(0,m)+'...': s; }
function Q(s){ return '"'+T(s).replace(/"/g,"'")+'"'; }
function find(doc,id){ var all=doc.getElementsByTagName('*'); for(var i=0;i<all.length;i++){ var a=all[i]; if(a.getAttribute && a.getAttribute('data-desk')==id) return a; }
  var fr=doc.parentWindow.frames; for(var k=0;k<fr.length;k++){ try{ var r=find(fr[k].document,id); if(r) return r; }catch(x){} } return null; }
function fire(e,name,code){ try{ if(e.fireEvent){ var ev=document.createEventObject(); if(code){ ev.keyCode=code; } e.fireEvent('on'+name,ev); }
  else { var v=document.createEvent('HTMLEvents'); v.initEvent(name,true,true); if(code){ v.keyCode=code; } e.dispatchEvent(v); } }catch(x){} }
"""

_SNAPSHOT = _COMMON + r"""
var GEN=%(gen)s, MAX=%(max)d, out=[], n=0;
function vis(e){ try{ if(!(e.offsetWidth>0||e.offsetHeight>0)) return false; var cs=e.currentStyle; if(cs&&cs.visibility=='hidden') return false; return true; }catch(x){ return false; } }
function idt(e){ return e.id? ' #'+e.id : (e.name? ' name='+e.name : ''); }
function lab(e){ var p=e.parentNode; return p? T(p.innerText,30) : ''; }
function rowtext(r){ var c=[]; for(var i=0;i<r.cells.length && i<12;i++) c.push(T(r.cells[i].innerText,14)); return c.join(' | '); }
function emit(e,key,disp){ n++; e.setAttribute('data-desk',GEN+'_'+n); out.push('@t'+n+'\t'+key+'\t'+disp); }
function walk(doc,fl){
  var all=doc.getElementsByTagName('*');
  for(var i=0;i<all.length;i++){
    if(n>=MAX){ out.push('#\t... more elements not shown (raise --max)'); return; }
    var e=all[i], t=e.tagName? e.tagName.toLowerCase():'', k=null, d=null;
    if(t=='input'){ var ty=(e.type||'text').toLowerCase(); if(ty=='hidden'||!vis(e)) continue;
      if(ty=='button'||ty=='submit'||ty=='reset'||ty=='image'){ k='button'+idt(e)+Q(e.value||e.alt||e.title); d='button '+Q(e.value||e.alt||e.title)+idt(e); }
      else if(ty=='checkbox'||ty=='radio'){ k=ty+idt(e)+lab(e); d=ty+(e.checked?' [x]':' [ ]')+' '+Q(lab(e))+idt(e); }
      else { k='input'+idt(e)+ty; d='input'+(ty!='text'?':'+ty:'')+idt(e)+' = '+(ty=='password'?'"***"':Q(e.value)); }
      if(e.disabled) d+=' (disabled)';
    } else if(t=='textarea'){ if(!vis(e)) continue; k='textarea'+idt(e); d='textarea'+idt(e)+' = '+Q(e.value); }
    else if(t=='select'){ if(!vis(e)) continue; var o=e.selectedIndex>=0? e.options[e.selectedIndex].text:''; k='select'+idt(e); d='select'+idt(e)+' = '+Q(o)+' ('+e.options.length+' options)'; }
    else if(t=='button'){ if(!vis(e)) continue; k='button'+idt(e)+Q(e.innerText); d='button '+Q(e.innerText)+idt(e); }
    else if(t=='a'){ if(!vis(e)) continue; var tx=T(e.innerText)||T(e.title); if(!tx){ var im=e.getElementsByTagName('img'); if(im.length) tx=T(im[0].alt)||T(im[0].title)||('[img '+String(im[0].src).replace(/^.*\//,'').replace(/\?.*$/,'')+']'); } if(!tx) continue; k='link'+idt(e)+tx; d='link '+Q(tx)+idt(e); }
    else if(t=='table'){ if(!e.rows||e.rows.length<2||e.getElementsByTagName('table').length>0||!vis(e)) continue; k='table'+idt(e)+rowtext(e.rows[0]); d='table'+idt(e)+' rows='+e.rows.length+' head: '+rowtext(e.rows[0]); }
    else if(t!='html'&&t!='body'&&t!='form'){ var oc=null; try{ oc=e.getAttribute('onclick'); }catch(x){} if(!oc||!vis(e)) continue; var tx2=T(e.innerText)||T(e.title)||T(e.alt); if(!tx2) tx2=(t=='img'? '[img]':''); if(!tx2) continue; k='click<'+t+'>'+idt(e)+tx2; d='clickable <'+t+'> '+Q(tx2)+idt(e); }
    if(k!==null) emit(e,fl+k,d);
  }
  var fr=doc.parentWindow.frames;
  for(var f=0;f<fr.length;f++){ var lbl=fl+'f'+f+'/';
    try{ var d2=fr[f].document; out.push('#\tframe '+f+': '+Q(d2.title)+' '+String(d2.URL).replace(/\?.*$/,'?...')); walk(d2,lbl); }
    catch(x){ out.push('#\tframe '+f+': (other domain, not readable)'); } }
}
walk(document,'');
return out.join('\n');
"""

_ACT = _COMMON + r"""
var ID=%(id)s, ACT=%(act)s, ARG=%(arg)s;
var e=find(document,ID); if(!e) return 'ERR stale ref - run snapshot again';
var t=e.tagName.toLowerCase();
if(ACT=='click'){ try{ e.focus(); }catch(x){} window.setTimeout(function(){ try{ e.click(); }catch(x){} },0); return 'OK click <'+t+'>'; }
if(ACT=='type'){
  if(t=='select'){ for(var i=0;i<e.options.length;i++){ var o=e.options[i]; if(T(o.text)==ARG||o.value==ARG){ e.selectedIndex=i; window.setTimeout(function(){ fire(e,'change'); },0); return 'OK select '+Q(o.text); } }
    var opts=[]; for(var j=0;j<e.options.length&&j<40;j++) opts.push(T(e.options[j].text,30)); return 'ERR no option '+Q(ARG)+'. options: '+opts.join(' / '); }
  try{ e.focus(); }catch(x){} e.value=ARG; window.setTimeout(function(){ fire(e,'keyup'); fire(e,'input'); fire(e,'change'); },0); return 'OK type <'+t+'>'; }
if(ACT=='key'){ var codes={enter:13,tab:9,esc:27,escape:27,space:32,backspace:8,up:38,down:40,left:37,right:39}; var c=codes[ARG.toLowerCase()]; if(!c) return 'ERR unknown key '+ARG;
  try{ e.focus(); }catch(x){} window.setTimeout(function(){ fire(e,'keydown',c); fire(e,'keypress',c); fire(e,'keyup',c); },0); return 'OK key '+ARG; }
if(ACT=='read'){
  if(t=='table'){ var rows=[]; for(var r=0;r<e.rows.length&&r<500;r++){ var cs=[]; for(var q=0;q<e.rows[r].cells.length;q++) cs.push(T(e.rows[r].cells[q].innerText,200)); rows.push(cs.join('\t')); } return rows.join('\n'); }
  if(t=='input'||t=='textarea') return (e.type=='password')? '***' : String(e.value);
  if(t=='select'){ var a=[]; for(var s=0;s<e.options.length;s++) a.push((s==e.selectedIndex?'* ':'  ')+e.options[s].text); return a.join('\n'); }
  var it=String(e.innerText||''); return it.length>20000? it.substring(0,20000)+'\n...(truncated)': it; }
if(ACT=='rect'){ var b=e.getBoundingClientRect(); return Math.round(b.left)+','+Math.round(b.top)+','+Math.round(b.right)+','+Math.round(b.bottom); }
return 'ERR unknown action '+ACT;
"""

_EVAL = r"""
var __v=eval(%(expr)s); if(__v===undefined) return 'undefined'; if(__v===null) return 'null'; return String(__v);
"""


def snapshot(ie_hwnd: int, gen: int, max_items: int = 300) -> list[tuple[str, str, str]]:
    """Return [(temp_id or '#', key, display)] for interactive elements of the page."""
    raw = run(ie_hwnd, _SNAPSHOT % {"gen": json.dumps(str(gen)), "max": max_items})
    if raw.startswith("ERR "):
        raise RuntimeError(raw)
    items = []
    for line in raw.split("\n"):
        if not line:
            continue
        parts = line.split("\t", 2)
        if parts[0] == "#":
            items.append(("#", "", parts[1] if len(parts) > 1 else ""))
        elif len(parts) == 3 and parts[0].startswith("@t"):
            items.append((f"{gen}_{parts[0][2:]}", parts[1], parts[2]))
    return items


def act(ie_hwnd: int, temp_id: str, action: str, arg: str = "") -> str:
    return run(ie_hwnd, _ACT % {"id": json.dumps(temp_id), "act": json.dumps(action), "arg": json.dumps(arg)})


def evaluate(ie_hwnd: int, expr: str) -> str:
    return run(ie_hwnd, _EVAL % {"expr": json.dumps(expr)})
