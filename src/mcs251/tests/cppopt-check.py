#!/usr/bin/env python3
"""Compile and execute the optimized Print register-copy regression."""
import argparse,hashlib,json,os,selectors,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--sdcc',required=True);p.add_argument('--qemu',required=True);p.add_argument('--work',type=Path,required=True)
a=p.parse_args();a.work.mkdir(parents=True,exist_ok=True)
source=Path(__file__).with_name('cppopt-assign-overlap.c')
inputs=[0,1,2,3,7,15,31,127,255,256,257,65535,65536,0x7fffffff,0x80000000,0xffffffff,0x12345678,0x89abcdef,0x01020304,0xffff0001]
calls=[];expected=[]
for base in [2,8,10,16,36]:
    for n in inputs:
        calls.append('format_u32(&table,0x%xUL,%d);'%(n,base))
        value=n;s=''
        while True:
            s='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'[value%base]+s;value//=base
            if not value:break
        expected.append(s)
main='''#include <stdint.h>
__sfr __at(0x99) SBUF;
extern uint32_t format_u32(void*,uint32_t,uint8_t);
static uint32_t emit(void* self,void* value,uint32_t length){uint32_t i;for(i=0;i<length;++i)SBUF=((char*)value)[i];SBUF='\\n';return length;}
static void* vtable[2]={0,(void*)emit};
static void* table=vtable;
void main(void){%s SBUF='D';SBUF='O';SBUF='N';SBUF='E';SBUF='\\n';for(;;){}}
'''%'\n'.join(calls)
(a.work/'main.c').write_text(main)
flags=['-mmcs251','--model-large','--stack-auto','--std-sdcc11','--nogcse','--opt-code-size']
for label,cmd in [('compile',[a.sdcc,*flags,'-c',str(source),'-o',str(a.work/'case.rel')]),('link',[a.sdcc,*flags,'--code-loc','0xff0000','--no-xinit-opt',str(a.work/'main.c'),str(a.work/'case.rel'),'-o',str(a.work/'image.hex')])]:
    result=subprocess.run(cmd,capture_output=True,text=True)
    (a.work/(label+'.log')).write_text(result.stdout+result.stderr)
    if result.returncode:raise SystemExit(result.returncode)
command=[a.qemu,'-M','stc32g144k246','-bios',str(a.work/'image.hex'),'-accel','tcg','-icount','shift=0,align=off,sleep=off','-display','none','-monitor','none','-serial','stdio']
process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
selector=selectors.DefaultSelector();selector.register(process.stdout,selectors.EVENT_READ);raw=b'';start=time.monotonic()
try:
    while time.monotonic()-start<20:
        if not selector.select(.1):continue
        data=os.read(process.stdout.fileno(),65536)
        if not data:break
        raw+=data
        if b'DONE\n' in raw:break
finally:
    selector.close();process.terminate();process.wait(timeout=2)
(a.work/'qemu.txt').write_bytes(raw)
oracle='\n'.join(expected+['DONE'])+'\n';(a.work/'expected.txt').write_text(oracle)
result={'status':'PASS' if raw.decode()==oracle else 'FAIL','comparisons':len(expected),'firmware_sha256':hashlib.sha256((a.work/'image.hex').read_bytes()).hexdigest(),'command':command}
(a.work/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
raise SystemExit(int(result['status']!='PASS'))
