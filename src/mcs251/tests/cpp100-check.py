#!/usr/bin/env python3
"""Run native-C reductions of four C++ lowering/backend miscompiles."""
import argparse,json,os,re,selectors,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sdcc',required=True);p.add_argument('--qemu',required=True);p.add_argument('--work',type=Path,required=True);a=p.parse_args();a.work.mkdir(parents=True,exist_ok=True)
values=[0,1,2,3,7,15,31,127,255,256,257,65535,65536,0x7fffffff,0x80000000,0xffffffff,0x12345678,0x89abcdef,0x01020304,0xffff0001]
def reference(n,x):
    if n==66:return x&0xffffff
    if n==14:
        s=x if x<2**31 else x-2**32
        return (s<0)+2*(s>-257)+4*(s==255)+8*(s<=32767)
    if n==23:return sum(i*x for i in range((x&15)+16) if not i&1)&0xffffffff
    return ((x^17)+((x>>8)&65535)*257+(x&255))&0xffffffff
reports=[]
for n in (14,23,46,66):
    name=str(n);image=a.work/(name+'.hex')
    source=Path(__file__).with_name('cpp100-pointer-init.c' if n==66 else 'cpp100-control-flow.c' if n==23 else 'cpp100-regressions.c')
    build=subprocess.run([a.sdcc,'-mmcs251','--model-large','--stack-auto','--std-sdcc11','--opt-code-size','--nogcse','--code-loc','0xff0000','--no-xinit-opt','-DTEST='+name,str(source),'-o',str(image)],capture_output=True,text=True)
    (a.work/(name+'.build.log')).write_text(build.stdout+build.stderr)
    if build.returncode:reports.append(dict(case=n,status='BUILD_FAIL'));continue
    process=subprocess.Popen([a.qemu,'-M','stc32g144k246','-bios',str(image),'-accel','tcg','-icount','shift=0,align=off,sleep=off','-display','none','-monitor','none','-serial','stdio'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    sel=selectors.DefaultSelector();sel.register(process.stdout,selectors.EVENT_READ);start=time.monotonic();output=b''
    try:
        while time.monotonic()-start<4:
            if not sel.select(.1):continue
            chunk=os.read(process.stdout.fileno(),65536)
            if not chunk:break
            output+=chunk
            if b'DONE' in output:break
    finally:
        sel.close();process.terminate()
        try:process.wait(timeout=1)
        except subprocess.TimeoutExpired:process.kill();process.wait()
    (a.work/(name+'.qemu.txt')).write_bytes(output)
    actual=[int(x,16) for x in re.findall(rb'^([0-9A-F]{8})\r?$',output,re.M)]
    expected=[reference(n,x) for x in values]
    reports.append(dict(case=n,status='PASS' if actual==expected and b'DONE' in output else 'FAIL',expected=expected,actual=actual))
(a.work/'results.json').write_text(json.dumps(reports,indent=2)+'\n')
print(json.dumps([{'case':r['case'],'status':r['status']} for r in reports]))
if any(r['status']!='PASS' for r in reports):raise SystemExit(1)
