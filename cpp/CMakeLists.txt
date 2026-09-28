cmake_minimum_required(VERSION 3.16)
project(mbdsdr VERSION 0.2.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Expose M_PI etc. from <cmath> on Windows toolchains (MSVC/MinGW);
# harmless on Linux where these are already defined.
add_compile_definitions(_USE_MATH_DEFINES)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O2")

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_AUTORCC ON)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Multimedia Network Concurrent Test)

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
    src/ui/waterfall.cpp
    src/ui/settings_dialog.cpp
    src/ui/about_dialog.cpp
    src/dsp/fft.cpp
    src/dsp/iq_buffer.cpp
    src/dsp/power_spectrum.cpp
    src/dsp/peak_detector.cpp
    src/dsp/noise_blanker.cpp
    src/dsp/rtl_tcp_source.cpp
    src/ui/bookmark_manager.cpp
    src/ui/shortcuts_dialog.cpp
    src/dsp/test_signal.cpp
    src/dsp/rtl_sdr_source.cpp
    src/dsp/spectrum_engine.cpp
    src/dsp/iq_frontend.cpp
    src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp
    src/dsp/squelch.cpp
    src/dsp/agc.cpp
    src/dsp/audio_output.cpp
    src/dsp/recorder.cpp
    src/dsp/wav_writer.cpp
    src/dsp/file_source.cpp
    src/dsp/gated_recorder.cpp
    src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp
    src/dsp/tle_client.cpp
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
    src/ui/waterfall.h
    src/dsp/source.h
    src/dsp/fft.h
    src/dsp/iq_buffer.h
    src/dsp/power_spectrum.h
    src/dsp/test_signal.h
    src/dsp/rtl_sdr_source.h
    src/dsp/spectrum_engine.h
    src/dsp/iq_frontend.h
    src/dsp/channelizer.h
    src/dsp/audio_resampler.h
    src/dsp/demod.h
    src/dsp/squelch.h
    src/dsp/agc.h
    src/dsp/audio_output.h
    src/dsp/recorder.h
    src/dsp/wav_writer.h
    src/dsp/file_source.h
    src/dsp/gated_recorder.h
)

add_executable(mbdsdr ${SOURCES} ${HEADERS})
target_include_directories(mbdsdr PRIVATE src)

# Offscreen screenshot tool (reuses all sources except main.cpp).
set(SHOT_SRCS ${SOURCES})
list(REMOVE_ITEM SHOT_SRCS src/main.cpp)
add_executable(ui_shot_r27 tests/ui_screenshot_r14.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_r27 PRIVATE src build/ui_shot_r27_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_r27 PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
target_link_libraries(mbdsdr PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

if(RTLSDR_FOUND)
    target_compile_definitions(mbdsdr PRIVATE HAVE_RTLSDR=1)
    target_include_directories(mbdsdr PRIVATE ${RTLSDR_INCLUDE_DIRS})
    target_link_directories(mbdsdr PRIVATE ${RTLSDR_LIBRARY_DIRS})
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

add_executable(test_peak_detector tests/test_peak_detector.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_peak_detector PRIVATE src)
target_link_libraries(test_peak_detector PRIVATE Qt6::Test Qt6::Core)
add_test(NAME peak_detector COMMAND test_peak_detector)

add_executable(test_noise_blanker tests/test_noise_blanker.cpp src/dsp/noise_blanker.cpp)
target_include_directories(test_noise_blanker PRIVATE src)
target_link_libraries(test_noise_blanker PRIVATE Qt6::Test Qt6::Core)
add_test(NAME noise_blanker COMMAND test_noise_blanker)

add_executable(test_engine_integration tests/test_engine_integration.cpp
    src/dsp/spectrum_engine.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_integration PRIVATE src)
target_link_libraries(test_engine_integration PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_integration COMMAND test_engine_integration)

add_executable(test_demod_e2e tests/test_demod_e2e.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp
    src/dsp/agc.cpp src/dsp/squelch.cpp src/dsp/fft.cpp)
target_include_directories(test_demod_e2e PRIVATE src)
target_link_libraries(test_demod_e2e PRIVATE Qt6::Core)
add_test(NAME demod_e2e COMMAND test_demod_e2e)

add_executable(test_bookmark tests/test_bookmark.cpp src/ui/bookmark_manager.cpp)
target_include_directories(test_bookmark PRIVATE src)
target_link_libraries(test_bookmark PRIVATE Qt6::Test Qt6::Core)
add_test(NAME bookmark COMMAND test_bookmark)

add_executable(test_spectrum_interaction tests/test_spectrum_interaction.cpp
    src/ui/spectrum_widget.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_spectrum_interaction PRIVATE src)
target_link_libraries(test_spectrum_interaction PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME spectrum_interaction COMMAND test_spectrum_interaction)

add_executable(test_channelizer tests/test_channelizer.cpp
    src/dsp/channelizer.cpp src/dsp/fft.cpp)
target_include_directories(test_channelizer PRIVATE src)
target_link_libraries(test_channelizer PRIVATE Qt6::Test Qt6::Core)
add_test(NAME channelizer COMMAND test_channelizer)

add_executable(test_chain tests/test_chain.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/fft.cpp)
target_include_directories(test_chain PRIVATE src)
target_link_libraries(test_chain PRIVATE Qt6::Test Qt6::Core)
add_test(NAME chain COMMAND test_chain)

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
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_agent PRIVATE src)
target_link_libraries(test_agent PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME agent COMMAND test_agent)

# GUI-dependent tests run on the offscreen platform so ctest works on
# headless/CI machines without a display.
set_tests_properties(recorder decoder agent PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

message(STATUS "MBDSDR C++ build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)")
