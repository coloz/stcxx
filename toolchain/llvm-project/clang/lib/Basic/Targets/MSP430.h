//===--- MSP430.h - Declare MSP430 target feature support -------*- C++ -*-===//
//
// Part of the LLVM Project, under the Apache License v2.0 with LLVM Exceptions.
// See https://llvm.org/LICENSE.txt for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception
//
//===----------------------------------------------------------------------===//
//
// This file declares MSP430 TargetInfo objects.
//
//===----------------------------------------------------------------------===//

#ifndef LLVM_CLANG_LIB_BASIC_TARGETS_MSP430_H
#define LLVM_CLANG_LIB_BASIC_TARGETS_MSP430_H

#include "clang/Basic/TargetInfo.h"
#include "clang/Basic/TargetOptions.h"
#include "llvm/Support/Compiler.h"
#include "llvm/TargetParser/Triple.h"

namespace clang {
namespace targets {

class LLVM_LIBRARY_VISIBILITY MSP430TargetInfo : public TargetInfo {
  static const char *const GCCRegNames[];

  bool isSTCMCS251IROnly() const {
    return getTriple().getArch() == llvm::Triple::msp430 &&
           getTriple().getVendorName() == "stc" &&
           getTriple().getOSName() == "none" &&
           getTriple().getEnvironment() == llvm::Triple::EABI;
  }

  bool isSTCMCS51IROnly() const {
    return getTriple().getArch() == llvm::Triple::msp430 &&
           getTriple().getVendorName() == "stc51" &&
           getTriple().getOSName() == "none" &&
           getTriple().getEnvironment() == llvm::Triple::EABI;
  }

  bool isSTCSDCCIROnly() const {
    return isSTCMCS251IROnly() || isSTCMCS51IROnly();
  }

public:
  MSP430TargetInfo(const llvm::Triple &Triple, const TargetOptions &)
      : TargetInfo(Triple) {
    // The msp430-stc-none-eabi triple is an IR-only frontend profile used by
    // the Arduino STC core.  Reusing the MSP430 arch avoids adding a new LLVM
    // backend enum while keeping the emitted IR on a target that llvm-cbe can
    // consume.  No MSP430 object code is emitted for this environment.
    if (isSTCSDCCIROnly()) {
      BigEndian = isSTCMCS251IROnly();
      TLSSupported = false;
      BoolWidth = 8;
      BoolAlign = 8;
      PointerWidth = 24;
      PointerAlign = 8;
      ShortWidth = 16;
      ShortAlign = 8;
      IntWidth = 16;
      IntAlign = 8;
      LongWidth = 32;
      LongAlign = 8;
      LongLongWidth = 64;
      LongLongAlign = 8;
      Int128Align = 8;
      SuitableAlign = 8;
      DefaultAlignForAttributeAligned = 8;
      NewAlign = 8;
      MaxVectorAlign = 8;
      HalfWidth = 16;
      HalfAlign = 8;
      FloatWidth = 32;
      FloatAlign = 8;
      DoubleWidth = 32;
      DoubleAlign = 8;
      DoubleFormat = &llvm::APFloat::IEEEsingle();
      LongDoubleWidth = 32;
      LongDoubleAlign = 8;
      LongDoubleFormat = &llvm::APFloat::IEEEsingle();
      Float128Align = 8;
      Ibm128Align = 8;
      SizeType = isSTCMCS251IROnly() ? UnsignedLong : UnsignedInt;
      PtrDiffType = SignedLong;
      IntPtrType = SignedLong;
      IntMaxType = SignedLongLong;
      WCharType = SignedInt;
      Char16Type = UnsignedInt;
      WIntType = SignedInt;
      Int64Type = SignedLongLong;
      Int16Type = SignedInt;
      Char32Type = UnsignedLong;
      SigAtomicType = SignedInt;
      if (isSTCMCS251IROnly())
        resetDataLayout(
            "E-m:e-p:24:8-i8:8-i16:8-i32:8-i64:8-i128:8-f32:8-f64:8-"
            "f128:8-a:8-n8:16:32-S8");
      else
        resetDataLayout(
            "e-m:e-p:24:8-p1:16:8-P1-i8:8-i16:8-i32:8-i64:8-i128:8-"
            "f32:8-f64:8-f128:8-a:8-n8:16:32-S8");
      return;
    }
    TLSSupported = false;
    IntWidth = 16;
    IntAlign = 16;
    LongWidth = 32;
    LongLongWidth = 64;
    LongAlign = LongLongAlign = 16;
    FloatWidth = 32;
    FloatAlign = 16;
    DoubleWidth = LongDoubleWidth = 64;
    DoubleAlign = LongDoubleAlign = 16;
    PointerWidth = 16;
    PointerAlign = 16;
    SuitableAlign = 16;
    SizeType = UnsignedInt;
    IntMaxType = SignedLongLong;
    IntPtrType = SignedInt;
    PtrDiffType = SignedInt;
    SigAtomicType = SignedLong;
    resetDataLayout("e-m:e-p:16:16-i32:16-i64:16-f32:16-f64:16-a:8-n8:16-S16");
  }
  void getTargetDefines(const LangOptions &Opts,
                        MacroBuilder &Builder) const override;

  ArrayRef<Builtin::Info> getTargetBuiltins() const override {
    // FIXME: Implement.
    return {};
  }

  bool allowsLargerPreferedTypeAlignment() const override { return false; }

  uint64_t getFunctionPointerWidth(LangAS AS) const override {
    return isSTCMCS51IROnly() && AS == LangAS::Default
               ? 16
               : TargetInfo::getFunctionPointerWidth(AS);
  }

  uint64_t getFunctionPointerAlign(LangAS AS) const override {
    return isSTCMCS51IROnly() && AS == LangAS::Default
               ? 8
               : TargetInfo::getFunctionPointerAlign(AS);
  }

  bool hasFeature(StringRef Feature) const override {
    if (isSTCMCS251IROnly())
      return Feature == "stc-mcs251";
    if (isSTCMCS51IROnly())
      return Feature == "stc-mcs51";
    return Feature == "msp430";
  }

  ArrayRef<const char *> getGCCRegNames() const override;

  ArrayRef<TargetInfo::GCCRegAlias> getGCCRegAliases() const override {
    if (isSTCSDCCIROnly())
      return {};
    // Make r0 - r3 be recognized by llc (f.e., in clobber list)
    static const TargetInfo::GCCRegAlias GCCRegAliases[] = {
        {{"r0"}, "pc"},
        {{"r1"}, "sp"},
        {{"r2"}, "sr"},
        {{"r3"}, "cg"},
    };
    return llvm::ArrayRef(GCCRegAliases);
  }

  bool validateAsmConstraint(const char *&Name,
                             TargetInfo::ConstraintInfo &info) const override {
    if (isSTCSDCCIROnly())
      return false;
    // FIXME: implement
    switch (*Name) {
    case 'K': // the constant 1
    case 'L': // constant -1^20 .. 1^19
    case 'M': // constant 1-4:
      return true;
    }
    // No target constraints for now.
    return false;
  }

  std::string_view getClobbers() const override {
    // FIXME: Is this really right?
    return "";
  }

  BuiltinVaListKind getBuiltinVaListKind() const override {
    // FIXME: implement
    return TargetInfo::CharPtrBuiltinVaList;
  }
};

} // namespace targets
} // namespace clang
#endif // LLVM_CLANG_LIB_BASIC_TARGETS_MSP430_H
