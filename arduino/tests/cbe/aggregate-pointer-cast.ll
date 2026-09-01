target datalayout = "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-f128:8-a:8-n8:16:32-S8"
target triple = "msp430-stc-none-eabi"

%union.anon = type { i16 }
%struct.stcxx_guard = type { i8, i8 }

@storage = internal global %union.anon zeroinitializer, align 1

define i8 @read_second_byte() {
entry:
  %value = load i8, ptr getelementptr inbounds (%struct.stcxx_guard, ptr @storage, i32 0, i32 1), align 1
  ret i8 %value
}
