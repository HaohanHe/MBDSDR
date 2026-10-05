# CMake toolchain file: cross-compile MBDSDR for arm64 (aarch64) on an x86_64 host.
#
# The Arm GNU prebuilt toolchain and the QEMU user emulator live OUTSIDE the
# repository under ~/.local/arm-cross (they are large and machine-specific;
# only this .cmake file is checked in). Paths here resolve relative to that
# install location via the ARM_CROSS_TC cache var or $HOME.
#
# Typical invocation:
#   cmake -S cpp -B cpp/build-arm64 \
#     -DCMAKE_TOOLCHAIN_FILE=cpp/cmake/aarch64-linux-gnu.cmake \
#     -DCMAKE_PREFIX_PATH=$HOME/.local/arm-cross/sysroot-qt
#
# Test execution (under QEMU user-mode emulation, on the x86 host):
#   ~/.local/arm-cross/usr/bin/qemu-aarch64 \
#     -L ~/.local/arm-cross/sysroot-qt \
#     cpp/build-arm64/<test-binary>
#
# NOTE: CMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER means build-time tools
# (moc/rcc/uic, cmake, make) are resolved from the host PATH so the x86_64
# versions run natively; only libraries/headers/packages are constrained to
# the arm64 sysroot(s).

set(CMAKE_SYSTEM_NAME Linux)
set(CMAKE_SYSTEM_PROCESSOR aarch64)

# ---- Locate the unpacked Arm GNU toolchain -------------------------------
# Override on the cmake command line with -DARM_CROSS_TC=/path/to/tc if the
# toolchain was unpacked somewhere other than the default location.
if(NOT DEFINED ARM_CROSS_TC OR NOT ARM_CROSS_TC)
    set(ARM_CROSS_TC "$ENV{HOME}/.local/arm-cross/arm-gnu-toolchain-13.3.rel1-x86_64-aarch64-none-linux-gnu")
endif()

if(NOT EXISTS "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-g++")
    message(FATAL_ERROR
        "Arm aarch64 toolchain not found at: ${ARM_CROSS_TC}\n"
        "Set -DARM_CROSS_TC=/path/to/arm-gnu-toolchain-... to the unpacked toolchain root.")
endif()

# The toolchain ships its own C library / libstdc++ sysroot.
set(CMAKE_SYSROOT "${ARM_CROSS_TC}/aarch64-none-linux-gnu/libc")

set(CMAKE_C_COMPILER   "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-gcc")
set(CMAKE_CXX_COMPILER "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-g++")
set(CMAKE_AR           "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-ar"      CACHE FILEPATH "" FORCE)
set(CMAKE_RANLIB       "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-ranlib"  CACHE FILEPATH "" FORCE)
set(CMAKE_LINKER       "${ARM_CROSS_TC}/bin/aarch64-none-linux-gnu-ld"      CACHE FILEPATH "" FORCE)

# ---- arm64 sysroot search path -------------------------------------------
# The toolchain C library sysroot plus (optionally) the hand-extracted
# arm64 Qt6 / librtlsdr sysroot. The latter is also supplied on the cmake
# command line via -DCMAKE_PREFIX_PATH; listing it here keeps find_library /
# find_package consistent regardless of how the caller sets PREFIX_PATH.
set(ARM_QT_SYSROOT "$ENV{HOME}/.local/arm-cross/sysroot-qt")
set(CMAKE_FIND_ROOT_PATH "${CMAKE_SYSROOT}" "${ARM_QT_SYSROOT}")

# Programs (moc/rcc/uic, compilers driving cmake) come from the HOST so the
# x86_64 executables run natively; headers/libraries/packages must stay inside
# the arm64 sysroot(s) so we never accidentally link against x86_64 system libs.
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# The unpacked Debian/Ubuntu arm64 sysroot(s) use the multiarch triplet
# "aarch64-linux-gnu" for their library/headers dirs. This Arm toolchain's own
# GNU triplet is "aarch64-none-linux-gnu", so CMake does NOT infer the multiarch
# lib dir automatically; without this, generic find_library/find_path (e.g.
# CMake's FindOpenGL) miss /usr/lib/aarch64-linux-gnu even though the libs are
# there. Pin it so all sysroot lookups use the Debian multiarch path.
set(CMAKE_LIBRARY_ARCHITECTURE "aarch64-linux-gnu")

# Qt6Core.so etc. carry DT_NEEDED on their own transitive runtime deps
# (libicu*.so.76, libglib-2.0.so.0, libpcre2-16.so.0, libzstd.so.1, libb2.so.1,
# libdouble-conversion.so.3, ...). The cross linker needs -rpath-link to follow
# THOSE libs' own DT_NEEDED chains (e.g. libicuuc -> libicudata,
# libglib -> libpcre2-8) at final link time; without it ld reports unresolved
# symbols inside Qt's shared libs.
set(_MBDSDR_ML "${ARM_QT_SYSROOT}/usr/lib/${CMAKE_LIBRARY_ARCHITECTURE}")
set(CMAKE_EXE_LINKER_FLAGS
    "${CMAKE_EXE_LINKER_FLAGS} -Wl,-rpath-link,${_MBDSDR_ML}")
set(CMAKE_SHARED_LINKER_FLAGS
    "${CMAKE_SHARED_LINKER_FLAGS} -Wl,-rpath-link,${_MBDSDR_ML}")

# The Arm toolchain ships its own gcc-13 libstdc++ + glibc 2.38, but the Debian
# arm64 Qt6 6.8.2 (and its deps, e.g. libsystemd) were built against Debian's
# gcc-14 libstdc++ / glibc 2.41 and need newer symbols (CXXABI_1.3.15,
# exp10@GLIBC_2.39, ...). Force the newer sysroot-qt libstdc++ and libm onto
# the end of the link line. We deliberately do NOT link sysroot-qt's libc.so.6:
# mixing Debian glibc 2.41 startup objects with the toolchain's glibc-2.38 crt
# breaks the compiler ABI check on GLIBC_PRIVATE tunables. The C runtime stays
# the toolchain's; only the C++ runtime and math lib come from the newer sysroot
# (newer libstdc++/libm are backward-compatible with gcc-13 objects). At runtime
# qemu -L sysroot-qt resolves every lib (including libc) from the Debian rootfs.
set(CMAKE_CXX_STANDARD_LIBRARIES
    "${_MBDSDR_ML}/libstdc++.so.6 ${_MBDSDR_ML}/libm.so.6"
    CACHE STRING "" FORCE)

# Position-independent code is the default for shared/PIC-friendly builds.
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

message(STATUS "MBDSDR aarch64 toolchain: TC=${ARM_CROSS_TC}")
message(STATUS "MBDSDR aarch64 sysroot:  CMAKE_SYSROOT=${CMAKE_SYSROOT}")
