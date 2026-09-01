# Validation-only compatibility shim for LLVM installations whose exported
# LLVMSupport target mentions zstd::libzstd_shared but whose package omitted
# the corresponding CMake target.  It is opt-in and does not download or find
# a library.  Callers must provide an exact existing shared-library path.
if(DEFINED STC_ZSTD_RUNTIME AND NOT TARGET zstd::libzstd_shared)
  if(NOT EXISTS "${STC_ZSTD_RUNTIME}")
    message(FATAL_ERROR "STC_ZSTD_RUNTIME does not exist: ${STC_ZSTD_RUNTIME}")
  endif()
  add_library(zstd::libzstd_shared UNKNOWN IMPORTED GLOBAL)
  set_target_properties(zstd::libzstd_shared PROPERTIES
    IMPORTED_LOCATION "${STC_ZSTD_RUNTIME}")
endif()
