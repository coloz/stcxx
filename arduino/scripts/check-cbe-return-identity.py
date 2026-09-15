#!/usr/bin/env python3
"""Execute CBE output to check that STC sret preserves caller-owned storage.

The host execution checks identity, not target pointer width. Arduino's
ReturnIdentity fixture independently checks the complete target pipeline.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


BODY = '''
%Object = type { ptr }
define void @make(ptr sret(%Object) %out) noinline {
  store ptr %out, ptr %out, align 1
  ret void
}
define void @forward(ptr sret(%Object) %out) noinline {
  call void @make(ptr sret(%Object) %out)
  ret void
}
define void @indirect(ptr sret(%Object) %out, ptr %fn) noinline {
  call void %fn(ptr sret(%Object) %out)
  ret void
}
define i32 @main() {
  %a = alloca %Object, align 1
  %b = alloca %Object, align 1
  %c = alloca %Object, align 1
  call void @make(ptr sret(%Object) %a)
  call void @forward(ptr sret(%Object) %b)
  call void @indirect(ptr sret(%Object) %c, ptr @make)
  %ap = load ptr, ptr %a, align 1
  %bp = load ptr, ptr %b, align 1
  %cp = load ptr, ptr %c, align 1
  %aeq = icmp eq ptr %ap, %a
  %beq = icmp eq ptr %bp, %b
  %ceq = icmp eq ptr %cp, %c
  %ab = and i1 %aeq, %beq
  %all = and i1 %ab, %ceq
  %failed = xor i1 %all, true
  %code = zext i1 %failed to i32
  ret i32 %code
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cbe', type=Path, required=True)
    parser.add_argument('--cc', default='gcc')
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    lock = json.loads((Path(__file__).resolve().parents[1] / 'toolchain-lock.json').read_text())
    report = {'status': 'RUNNING', 'cbe_sha256': hashlib.sha256(args.cbe.read_bytes()).hexdigest(), 'checks': []}
    output = args.work / 'return-identity.json'
    output.write_text(json.dumps(report, indent=2) + '\n')
    try:
        for profile, triple in [('mcs251', 'msp430-stc-none-eabi'), ('mcs51', 'msp430-stc51-none-eabi')]:
            ir = args.work / (profile + '.ll')
            source = args.work / (profile + '.c')
            body = BODY if profile == 'mcs251' else BODY.replace(
                'ptr %fn', 'ptr addrspace(1) %fn').replace('ptr @make', 'ptr addrspace(1) @make')
            ir.write_text('target datalayout = "' + lock['profiles'][profile]['data_layout'] + '"\n'
                          'target triple = "' + triple + '"\n' + body)
            subprocess.run([str(args.cbe), str(ir), '-o', str(source)], check=True, timeout=30)
            for level in ('0', '2'):
                binary = args.work / (profile + '-O' + level)
                compiled = subprocess.run([args.cc, '-std=c11', '-O' + level, str(source), '-o', str(binary)], capture_output=True, text=True, timeout=30)
                binary.with_suffix('.log').write_text(compiled.stdout + compiled.stderr)
                if compiled.returncode:
                    raise RuntimeError('host compilation failed: ' + str(binary))
                run = subprocess.run([str(binary)], timeout=10)
                report['checks'].append({'profile': profile, 'host_optimization': level,
                                          'status': 'PASS' if run.returncode == 0 else 'FAIL',
                                          'exit_code': run.returncode,
                                          'ir_sha256': hashlib.sha256(ir.read_bytes()).hexdigest(),
                                          'c_sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
        report['status'] = 'PASS' if all(item['status'] == 'PASS' for item in report['checks']) else 'FAIL'
    except Exception as error:
        report['status'], report['error'] = 'FAIL', str(error)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
