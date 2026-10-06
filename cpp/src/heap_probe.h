// SPDX-License-Identifier: MIT
//
// heap_probe.h -- lightweight allocation-counting probe (NO valgrind).
//
// Overrides the global operator new/delete and keeps ATOMIC COUNTERS of live
// allocations and cumulative allocations. It routes through plain malloc/free,
// so runtime behaviour is identical to the default allocator -- the only cost
// is two relaxed atomic adds per allocation. It does NOT track byte sizes
// (operator delete receives no size; a size map would itself allocate and
// recurse), but a growing LIVE allocation count is exactly the signature of a
// C++ object leak, and a flat live count with growing RSS points at glibc
// arena / mmap retention rather than an application leak.
//
// The 10-second printer is installed from main() ONLY when the env var
// MBDSDR_HEAP_PROBE=1 is set, so normal builds/tests never print.
#pragma once

namespace mbdsdr {
namespace probe {

// Live number of outstanding allocations (objects not yet deleted).
long long liveAllocations();
// Cumulative allocations since process start (monotonic).
long long totalAllocations();

// Start a QTimer (on the calling thread's event loop) that prints
// "[heapprobe] t=<sec> live=<n> total=<n>" every 10 s. No-op if already
// installed. Called once from main() under the env gate.
void installPrinter();

} // namespace probe
} // namespace mbdsdr
