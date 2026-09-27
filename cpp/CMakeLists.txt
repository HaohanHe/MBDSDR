cmake_minimum_required(VERSION 3.16)
project(mbdsdr LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O2")

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Multimedia Network Test)

set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})

find_package(PkgConfig QUIET)
if(PkgConfig_FOUND)
    pkg_check_modules(RTLSDR librtlsdr)
endif()

set(SOURCES
    src/main.cpp
    src/ui/main_window.cpp
    src/ui/spectrum_widget.cpp
    src/ui/sky_view.cpp
    src/ui/world_view.cpp
    src/dsp/fft.cpp
    src/dsp/iq_buffer.cpp
    src/dsp/power_spectrum.cpp
    src/dsp/test_signal.cpp
    src/dsp/rtl_sdr_source.cpp
    src/dsp/spectrum_engine.cpp
    src/dsp/iq_frontend.cpp
    src/dsp/demod.cpp
    src/dsp/squelch.cpp
    src/dsp/agc.cpp
    src/dsp/audio_output.cpp
    src/dsp/recorder.cpp
    src/dsp/file_source.cpp
    src/dsp/gated_recorder.cpp
    src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp
    src/ai/llm_client.cpp
    src/ai/llm_worker.cpp
    src/ai/ai_config.cpp
    src/ai/agent_tools.cpp
    src/ai/agent.cpp
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
    src/dsp/iq_frontend.h
    src/dsp/demod.h
    src/dsp/squelch.h
    src/dsp/agc.h
    src/dsp/audio_output.h
    src/dsp/recorder.h
    src/dsp/file_source.h
    src/dsp/gated_recorder.h
)

add_executable(mbdsdr ${SOURCES} ${HEADERS})
target_include_directories(mbdsdr PRIVATE src)
target_link_libraries(mbdsdr PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets Qt6::Multimedia Qt6::Network)

if(RTLSDR_FOUND)
    target_compile_definitions(mbdsdr PRIVATE HAVE_RTLSDR=1)
    target_include_directories(mbdsdr PRIVATE ${RTLSDR_INCLUDE_DIRS})
    target_link_libraries(mbdsdr PRIVATE ${RTLSDR_LIBRARIES})
    message(STATUS "RTL-SDR support: ENABLED (librtlsdr ${RTLSDR_VERSION})")
else()
    message(STATUS "RTL-SDR support: DISABLED (librtlsdr not found; stub compiled)")
endif()

# ---- Tests ----
enable_testing()
add_executable(test_fft tests/test_fft.cpp src/dsp/fft.cpp)
target_include_directories(test_fft PRIVATE src)
target_link_libraries(test_fft PRIVATE Qt6::Test Qt6::Core)
add_test(NAME fft COMMAND test_fft)

add_executable(test_demod tests/test_demod.cpp
    src/dsp/fft.cpp src/dsp/demod.cpp src/dsp/squelch.cpp src/dsp/agc.cpp)
target_include_directories(test_demod PRIVATE src)
target_link_libraries(test_demod PRIVATE Qt6::Test Qt6::Core)
add_test(NAME demod COMMAND test_demod)

add_executable(test_recorder tests/test_recorder.cpp
    src/dsp/recorder.cpp src/dsp/file_source.cpp src/dsp/gated_recorder.cpp
    src/dsp/demod.cpp)
target_include_directories(test_recorder PRIVATE src)
target_link_libraries(test_recorder PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME recorder COMMAND test_recorder)

add_executable(test_decoder tests/test_decoder.cpp
    src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_decoder PRIVATE src)
target_link_libraries(test_decoder PRIVATE Qt6::Test Qt6::Core)
add_test(NAME decoder COMMAND test_decoder)

add_executable(test_agent tests/test_agent.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/ai_config.cpp
    src/ai/agent_tools.cpp src/ai/agent.cpp src/dsp/spectrum_engine.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/demod.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/recorder.cpp src/dsp/gated_recorder.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/fft.cpp src/dsp/power_spectrum.cpp)
target_include_directories(test_agent PRIVATE src)
target_link_libraries(test_agent PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME agent COMMAND test_agent)

message(STATUS "MBDSDR C++ build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)")
