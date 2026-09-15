#include <stdint.h>
#include <string.h>
typedef unsigned char bool;
#define __forceinline inline
struct l_array_33_uint8_t { uint8_t array[33]; };
typedef uint32_t l_fptr_12(void*,void*,uint32_t);


static __forceinline uint8_t llvm_select_u8(bool condition, uint8_t iftrue, uint8_t ifnot) {
  uint8_t r = condition ? iftrue : ifnot;
  return r;
}

static __forceinline uint8_t llvm_add_u8(uint8_t a, uint8_t b) {
  uint8_t r = a + b;
  return r;
}

static __forceinline uint32_t llvm_sub_u32(uint32_t a, uint32_t b) {
  uint32_t r = a - b;
  return r;
}

static __forceinline uint32_t llvm_mul_u32(uint32_t a, uint32_t b) {
  uint32_t r = a * b;
  return r;
}

static __forceinline uint32_t llvm_udiv_u32(uint32_t a, uint32_t b) {
  uint32_t r = a / b;
  return r;
}

static __forceinline uint8_t llvm_or_u8(uint8_t a, uint8_t b) {
  uint8_t r = a | b;
  return r;
}

uint32_t format_u32(void* _194, uint32_t _195, uint8_t _196) {
  struct l_array_33_uint8_t _197;    /* Address-exposed local */
  void* _198;
  uint32_t _199;
  void* _200;
  void* _200__PHI_TEMPORARY;
  uint32_t _201;
  uint32_t _201__PHI_TEMPORARY;
  uint32_t _202;
  uint32_t _203;
  uint32_t _204;
  uint8_t _205;
  void* _206;
  uint32_t _207;
  void* _208;
  void* _209;
  uint32_t _210;

  _198 = ((&((uint8_t*)(&_197))[((signed _BitInt(24))32)]));
  *(uint8_t*)_198 = 0;
  _199 = ((uint32_t)(uint8_t)(llvm_select_u8((((uint8_t)(llvm_add_u8(_196, -37))) < ((uint8_t)((uint8_t)-35))), 10, _196)));
  _200__PHI_TEMPORARY = _198;   /* for PHI node */
  _201__PHI_TEMPORARY = _195;   /* for PHI node */
  goto _211;

  do {     /* Syntactic loop '' to make GCC happy */
_211:
  _200 = _200__PHI_TEMPORARY;
  _201 = _201__PHI_TEMPORARY;
  _202 = _201;
  _203 = llvm_udiv_u32(_202, _199);
  _204 = llvm_sub_u32(_202, (llvm_mul_u32(_203, _199)));
  _205 = ((uint8_t)_204);
  _206 = ((&((uint8_t*)_200)[((signed _BitInt(24))-1)]));
  *(uint8_t*)_206 = (llvm_select_u8((((uint32_t)_204) < ((uint32_t)10u)), (llvm_or_u8(_205, 48)), (llvm_add_u8(_205, 55))));
  if ((((uint32_t)_201) < ((uint32_t)_199))) {
    goto _212;
  } else {
    _200__PHI_TEMPORARY = _206;   /* for PHI node */
    _201__PHI_TEMPORARY = _203;   /* for PHI node */
    goto _211;
  }

  } while (1); /* end of syntactic loop '' */
_212:
  _207 = strlen(_206);
  _208 = *(void**)_194;
  _209 = *(void**)(((uint8_t*)_208) + (((signed _BitInt(24))3)));
  _210 = ((l_fptr_12*)(void*)_209)(_194, _206, _207);
  return _210;
}
