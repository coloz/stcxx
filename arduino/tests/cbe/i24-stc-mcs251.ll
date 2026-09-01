target datalayout = "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-f128:8-a:8-n8:16:32-S8"
target triple = "msp430-stc-none-eabi"

%member_pointer = type { i24, i24 }

@member_pointer_value = global %member_pointer { i24 1, i24 0 }, align 1

define i24 @identity_i24(i24 %value) {
entry:
  ret i24 %value
}
