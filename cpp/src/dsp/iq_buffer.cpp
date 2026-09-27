// SPDX-License-Identifier: MIT
#include "iq_buffer.h"

#include <algorithm>
#include <cstring>

namespace mbdsdr {
namespace dsp {

IQBuffer::IQBuffer(std::size_t capacity)
    : buf_(capacity), head_(0), count_(0) {}

void IQBuffer::push(const std::complex<float>* src, std::size_t n) {
    if (n == 0) return;
    const std::size_t cap = buf_.size();
    for (std::size_t i = 0; i < n; ++i) {
        buf_[head_] = src[i];
        head_ = (head_ + 1) % cap;
        if (count_ < cap) ++count_;
    }
}

std::size_t IQBuffer::latest(std::vector<std::complex<float>>& dst,
                             std::size_t n) const {
    const std::size_t cap = buf_.size();
    const std::size_t avail = std::min(n, count_);
    if (dst.size() < n) dst.resize(n);
    if (avail == 0) return 0;

    // The most recent `avail` samples end at head_ (exclusive).
    // Walk backwards.
    std::size_t idx = (head_ + cap - avail) % cap;
    for (std::size_t i = 0; i < avail; ++i) {
        dst[i] = buf_[idx];
        idx = (idx + 1) % cap;
    }
    return avail;
}

} // namespace dsp
} // namespace mbdsdr
