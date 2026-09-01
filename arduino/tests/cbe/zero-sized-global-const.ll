target datalayout = "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-f128:8-a:8-n8:16:32-S8"
target triple = "msp430-stc-none-eabi"

@constant_storage = external constant [0 x i8], align 1
@mutable_storage = external global [0 x i8], align 1

declare void @consume(ptr)

define void @pass_both() {
entry:
  call void @consume(ptr @constant_storage)
  call void @consume(ptr @mutable_storage)
  ret void
}
