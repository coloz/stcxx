#!/usr/bin/env python3
"""Check CBE shift width, scalar freeze proofs and Release-mode diagnostics."""
import argparse
import json
from pathlib import Path
import subprocess

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--cbe',required=True)
p.add_argument('--work',type=Path,required=True)
a=p.parse_args();a.work.mkdir(parents=True,exist_ok=True)
profiles=json.loads((Path(__file__).resolve().parents[1]/'toolchain-lock.json').read_text())['profiles']
tests={
 'erased_marker_before_static_call': ('declare void @llvm.assume(i1)\ndefine internal void @callee() {\n ret void\n}\ndefine void @f(i1 noundef %c) {\n call void @llvm.assume(i1 %c)\n call void @callee()\n ret void\n}',True,'static void callee'),
 'shift32': ('define i32 @f(i32 %n) {\n %x = shl i32 1, %n\n ret i32 %x\n}',True,'llvm_shl_u32'),
 'shift64': ('define i64 @f(i64 %n) {\n %x = shl i64 1, %n\n ret i64 %x\n}',True,'llvm_shl_u64'),
 'freeze_argument': ('define i32 @f(i32 noundef %n) {\n %x = freeze i32 %n\n ret i32 %x\n}',True,''),
 'freeze_loop': ('''define i32 @f(i32 noundef %n, i8 noundef %base) {
entry:
 %b = zext nneg i8 %base to i32
 br label %loop
loop:
 %v = phi i32 [ %n, %entry ], [ %q, %loop ]
 %f = freeze i32 %v
 %q = udiv i32 %f, %b
 %done = icmp ult i32 %v, %b
 br i1 %done, label %exit, label %loop
exit:
 ret i32 %q
}''',True,''),
 'freeze_unknown_argument': ('define i32 @f(i32 %n) {\n %x = freeze i32 %n\n ret i32 %x\n}',False,'freeze operand is not proven defined'),
 'freeze_poison': ('define i32 @f() {\n %x = freeze i32 poison\n ret i32 %x\n}',False,'freeze operand is not proven defined'),
 'freeze_undef': ('define i32 @f() {\n %x = freeze i32 undef\n ret i32 %x\n}',False,'freeze operand is not proven defined'),
 'freeze_flagged_add': ('define i32 @f(i32 noundef %n) {\n %v = add nsw i32 %n, 1\n %x = freeze i32 %v\n ret i32 %x\n}',False,'freeze operand is not proven defined'),
 'freeze_unbounded_shift': ('define i32 @f(i32 noundef %n) {\n %v = shl i32 1, %n\n %x = freeze i32 %v\n ret i32 %x\n}',False,'freeze operand is not proven defined'),
}
float_loop = '''@out = global i32 0
define i32 @f(float noundef %x) {
entry:
 %initial = fptoui float %x to i32
 br label %loop
loop:
 %v = phi i32 [ %initial, %entry ], [ %q, %loop ]
 %f = freeze i32 %v
 %q = udiv i32 %f, 10
 store i32 %q, ptr @out
 %done = icmp ult i32 %v, 10
 br i1 %done, label %exit, label %loop
exit:
 ret i32 %f
}'''
tests.update({
 'freeze_bounded_narrow_loop': ('''define i16 @f() {
entry:
 br label %loop
loop:
 %i = phi i3 [ 0, %entry ], [ %next, %loop ]
 %wide = zext i3 %i to i16
 %product = mul nuw nsw i16 %wide, 7
 %frozen = freeze i16 %product
 %next = add i3 %i, 1
 %done = icmp eq i3 %next, 0
 br i1 %done, label %exit, label %loop
exit:
 ret i16 %frozen
}''', True, ''),
 'freeze_bounded_add': ('define i16 @f(i3 noundef %x) {\n %wide = zext i3 %x to i16\n %sum = add nuw nsw i16 %wide, 32760\n %f = freeze i16 %sum\n ret i16 %f\n}', True, ''),
 'freeze_bounded_mul_overflow': ('define i16 @f(i3 noundef %x) {\n %wide = zext i3 %x to i16\n %sum = mul nuw nsw i16 %wide, 10000\n %f = freeze i16 %sum\n ret i16 %f\n}', False, 'freeze operand is not proven defined'),
 'freeze_bounded_signed_overflow': ('define i16 @f(i3 noundef %x) {\n %wide = zext i3 %x to i16\n %sum = add nuw nsw i16 %wide, 32767\n %f = freeze i16 %sum\n ret i16 %f\n}', False, 'freeze operand is not proven defined'),
 'freeze_float_loop_branch': (float_loop, True, ''),
 'freeze_float_loop_frozen_branch': (float_loop.replace('icmp ult i32 %v', 'icmp ult i32 %f'), False, 'freeze operand is not proven defined'),
 'freeze_undef_loop_branch': (float_loop.replace('[ %initial, %entry ]', '[ undef, %entry ]'), False, 'freeze operand is not proven defined'),
 'freeze_float_loop_implicit_exit': ('declare void @maybe_exit()\n'+float_loop.replace(' %done =', ' call void @maybe_exit()\n %done ='), False, 'freeze operand is not proven defined'),
})
results=[]
for bits in (8, 16, 24, 32, 64):
    for op in ('fshl', 'fshr'):
        body=('declare i{w} @llvm.{op}.i{w}(i{w}, i{w}, i{w})\n'
              'define i{w} @f(i{w} %a, i{w} %b, i{w} %n) {{\n'
              ' %r = call i{w} @llvm.{op}.i{w}(i{w} %a, i{w} %b, i{w} %n)\n'
              ' ret i{w} %r\n}}').format(w=bits,op=op)
        tests[op+str(bits)]=(body,True,'c = c % '+str(bits))
for bits in (8,16,32,64):
    body=('declare i{w} @llvm.abs.i{w}(i{w}, i1 immarg)\n'
          'define i{w} @f(i{w} %a) {{\n'
          ' %r = call i{w} @llvm.abs.i{w}(i{w} %a, i1 false)\n'
          ' ret i{w} %r\n}}').format(w=bits)
    tests['abs'+str(bits)]=(body,True,'(0u - a) : a')
for target in ('mcs251','mcs51'):
    triple='msp430-stc-none-eabi' if target=='mcs251' else 'msp430-stc51-none-eabi'
    layout=profiles[target]['data_layout']
    for name,(body,success,marker) in tests.items():
        stem=a.work/(target+'-'+name);ir=stem.with_suffix('.ll');output=stem.with_suffix('.c')
        ir.write_text('target datalayout = "%s"\ntarget triple = "%s"\n%s\n'%(layout,triple,body))
        proc=subprocess.run([a.cbe,str(ir),'-o',str(output)],capture_output=True,text=True,timeout=20)
        stem.with_suffix('.log').write_text(proc.stdout+proc.stderr)
        good=(proc.returncode==0 and marker in output.read_text()) if success else (proc.returncode>0 and marker in proc.stderr)
        if good and name=='erased_marker_before_static_call':
            typedefs=output.read_text().split('/* Function definitions */')[1].split('/* Types Definitions */')[0]
            good='callee(' not in typedefs
        results.append(dict(target=target,test=name,status='PASS' if good else 'FAIL',exit_code=proc.returncode))
(a.work/'results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
raise SystemExit(int(any(r['status']!='PASS' for r in results)))
