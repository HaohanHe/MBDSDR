// SPDX-License-Identifier: MIT
// Simple circular IQ sample buffer (complex float).
#pragma once

#include <complex>
#include <vector>
#include <cstddef>

namespace mbdsdr {
namespace dsp {

class IQBuffer {
public:
    explicit IQBuffer(std::size_t capacity);

    /// Push n samples (overwrite oldest when full).
    void push(const std::complex<float>* src, std::size_t n);

    /// Copy the most recent n samples into dst (dst must have size >= n).
    /// Returns the number of samples actually available (may be < n if
    /// not enough data has been written yet).
    std::size_t latest(std::vector<std::complex<float>>& dst, std::size_t n) const;

    std::size_t size() const { return count_; }
    std::size_t capacity() const { return buf_.size(); }

private:
    std::vector<std::complex<float>> buf_;
    std::size_t head_ = 0;   // next write index
    std::size_t count_ = 0;  // samples currently stored
};

} // namespace dsp
} // namespace mbdsdr
