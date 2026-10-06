// SPDX-License-Identifier: MIT
#include "heap_probe.h"

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <new>
#include <chrono>
#include <unistd.h>
#include <malloc.h>

#include <QTimer>
#include <QCoreApplication>

namespace mbdsdr {
namespace probe {
namespace {

std::atomic<long long> g_live{0};
std::atomic<long long> g_total{0};
bool g_printerInstalled = false;

} // namespace

long long liveAllocations()  { return g_live.load(std::memory_order_relaxed); }
long long totalAllocations() { return g_total.load(std::memory_order_relaxed); }

void installPrinter() {
    if (g_printerInstalled) return;
    g_printerInstalled = true;
    const bool doTrim = qgetenv("MBDSDR_HEAP_TRIM") == "1";
    auto* t = new QTimer();
    const auto t0 = std::chrono::steady_clock::now();
    QObject::connect(t, &QTimer::timeout, [t0, doTrim]() {
        if (doTrim) ::malloc_trim(0);
        // Resident pages from /proc/self/statm (page 2 = resident set).
        long rssPages = 0;
        if (FILE* f = std::fopen("/proc/self/statm", "r")) {
            std::fscanf(f, "%*ld %ld", &rssPages);
            std::fclose(f);
        }
        // glibc arena split: arena = sbrk heap bytes, hblkhd = mmap bytes.
        struct mallinfo2 mi = ::mallinfo2();
        const double sec =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - t0)
                .count();
        std::fprintf(stderr,
                     "[heapprobe] t=%.0f live=%lld total=%lld rss_kb=%ld "
                     "arena_kb=%ld mmap_kb=%ld%s\n",
                     sec,
                     g_live.load(std::memory_order_relaxed),
                     g_total.load(std::memory_order_relaxed),
                     rssPages * (long)(::sysconf(_SC_PAGESIZE) / 1024),
                     (long)(mi.arena / 1024),
                     (long)(mi.hblkhd / 1024),
                     doTrim ? " trim=1" : "");
        std::fflush(stderr);
    });
    t->start(10000);   // every 10 s
}
} // namespace probe
} // namespace mbdsdr

// ---- Global allocator override (count-only; malloc/free underneath) --------
// Routed through std::malloc/std::free so behaviour matches the default
// allocator. Relaxed atomics only -- no ordering needed for a counter.

void* operator new(std::size_t n) {
    mbdsdr::probe::g_total.fetch_add(1, std::memory_order_relaxed);
    mbdsdr::probe::g_live.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n ? n : 1)) return p;
    throw std::bad_alloc();
}
void operator delete(void* p) noexcept {
    if (p) mbdsdr::probe::g_live.fetch_sub(1, std::memory_order_relaxed);
    std::free(p);
}
void operator delete(void* p, std::size_t) noexcept {
    if (p) mbdsdr::probe::g_live.fetch_sub(1, std::memory_order_relaxed);
    std::free(p);
}
void* operator new[](std::size_t n) { return operator new(n); }
void operator delete[](void* p) noexcept { operator delete(p); }
void operator delete[](void* p, std::size_t) noexcept { operator delete(p); }
