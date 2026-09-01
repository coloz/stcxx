target datalayout = "e-m:e-p:64:64-i64:64-n8:16:32:64-S128"
target triple = "x86_64-unknown-linux-gnu"

%pair24 = type { i24, i24 }

@pair24_value = global %pair24 { i24 1, i24 0 }

define i24 @identity_i24(i24 %value) {
entry:
  ret i24 %value
}
