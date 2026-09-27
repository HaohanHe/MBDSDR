cmake_minimum_required(VERSION 3.16)
project(mbdsdr LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O2")

# Qt6
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Test)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

# ---- Optional RTL-SDR support ----
find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(RTLSDR librtlsdr)
endif()

set(SOURCES
    src/main.cpp
    src/ui/main_window.cpp
    src/ui/spectrum_widget.cpp
    src/dsp/fft.cpp
    src/dsp/iq_buffer.cpp
    src/dsp/power_spectrum.cpp
    src/dsp/test_signal.cpp
    src/dsp/rtl_sdr_source.cpp
    src/dsp/spectrum_engine.cpp
)

set(HEADERS
    src/core/tokens.h
    src/core/spectrum_frame.h
    src/ui/main_window.h
    src/ui/spectrum_widget.h
    src/dsp/source.h
    src/dsp/fft.h
    src/dsp/iq_buffer.h
    src/dsp/power_spectrum.h
    src/dsp/test_signal.h
    src/dsp/rtl_sdr_source.h
    src/dsp/spectrum_engine.h
)

add_executable(mbdsdr ${SOURCES} ${HEADERS})
target_include_directories(mbdsdr PRIVATE src)
target_link_libraries(mbdsdr PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets)

if(RTLSDR_FOUND)
    target_compile_definitions(mbdsdr PRIVATE HAVE_RTLSDR=1)
    target_include_directories(mbdsdr PRIVATE ${RTLSDR_INCLUDE_DIRS})
    target_link_libraries(mbdsdr PRIVATE ${RTLSDR_LIBRARIES})
    message(STATUS "RTL-SDR support: ENABLED (librtlsdr ${RTLSDR_VERSION})")
else()
    message(STATUS "RTL-SDR support: DISABLED (librtlsdr not found; stub compiled)")
endif()

# ---- FFT unit tests ----
enable_testing()
add_executable(test_fft tests/test_fft.cpp src/dsp/fft.cpp)
target_include_directories(test_fft PRIVATE src)
target_link_libraries(test_fft PRIVATE Qt6::Test Qt6::Core)
add_test(NAME fft COMMAND test_fft)

message(STATUS "MBDSDR C++ build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)")
