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

# Fallback when pkg-config is unavailable (e.g. plain MinGW on Windows):
# locate the rtl-sdr header and import library directly via CMAKE_PREFIX_PATH.
if(NOT RTLSDR_FOUND)
    find_path(RTLSDR_INCLUDE_DIR NAMES rtl-sdr.h)
    find_library(RTLSDR_LIBRARY NAMES librtlsdr rtlsdr librtlsdr.dll.a)
    if(RTLSDR_INCLUDE_DIR AND RTLSDR_LIBRARY)
        set(RTLSDR_FOUND TRUE)
        set(RTLSDR_INCLUDE_DIRS "${RTLSDR_INCLUDE_DIR}")
        set(RTLSDR_LIBRARIES "${RTLSDR_LIBRARY}")
        get_filename_component(RTLSDR_LIB_DIR "${RTLSDR_LIBRARY}" DIRECTORY)
        set(RTLSDR_LIBRARY_DIRS "${RTLSDR_LIB_DIR}")
    endif()
endif()

set(SOURCES
    src/main.cpp
    src/ui/main_window.cpp
    src/ui/spectrum_widget.cpp
    src/ui/spectrum_display.cpp
    src/ui/sky_view.cpp
    src/ui/world_view.cpp
    src/ui/aircraft_tracker.cpp
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
    src/dsp/vfo_manager.cpp
    src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp
    src/dsp/wfm_stereo.cpp
    src/dsp/digital_demod.cpp
    src/dsp/anr.cpp
    src/ui/constellation_view.cpp
    src/ui/weather_panel.cpp
    src/dsp/squelch.cpp
    src/dsp/agc.cpp
    src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/memory_audio_sink.cpp
    src/dsp/recorder.cpp
    src/dsp/wav_writer.cpp
    src/dsp/file_source.cpp
    src/dsp/gated_recorder.cpp
    src/dsp/signal_watch.cpp
    src/dsp/frequency_scanner.cpp
    src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp
    src/dsp/rds_decoder.cpp
    src/dsp/apt_decoder.cpp
    src/dsp/tle_client.cpp
    src/dsp/sgp4.cpp
    src/gnss/serial_transport.cpp
    src/gnss/stream_transport.cpp
    src/gnss/nmea_parser.cpp
    src/gnss/gnss_receiver.cpp
    src/ui/elevation_plot.cpp
    src/ui/map_projection.cpp
    src/ai/llm_client.cpp
    src/ai/llm_worker.cpp
    src/ai/ai_config.cpp
    src/ai/agent_tools.cpp
    src/ai/agent.cpp
    src/tx/modulator.cpp
    src/tx/loopback_backend.cpp
    src/tx/tx_controller.cpp
    src/tx/soapy_tx_backend.cpp
    src/radio/ax25.cpp
    src/radio/kiss.cpp
    src/radio/radio_link.cpp
    src/radio/cat_client.cpp
    src/radio/cw_keyer.cpp
    src/ui/radio_panel.cpp
)

set(HEADERS
    src/core/tokens.h
    src/core/spectrum_frame.h
    src/ui/main_window.h
    src/ui/spectrum_widget.h
    src/ui/spectrum_display.h
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
    src/dsp/wfm_stereo.h
    src/dsp/squelch.h
    src/dsp/agc.h
    src/dsp/audio_output.h
    src/dsp/iaudio_sink.h
    src/dsp/qt_audio_sink.h
    src/dsp/memory_audio_sink.h
    src/dsp/recorder.h
    src/dsp/wav_writer.h
    src/dsp/file_source.h
    src/dsp/gated_recorder.h
    src/dsp/signal_watch.h
    src/dsp/frequency_scanner.h
    src/dsp/sgp4.h
    src/ui/sky_view.h
    src/ui/world_view.h
    src/ui/aircraft_tracker.h
    src/ui/weather_panel.h
    src/ui/map_projection.h
    src/ui/coastline_data.h
    src/ui/elevation_plot.h
    src/gnss/gnss_types.h
    src/gnss/i_transport.h
    src/gnss/serial_transport.h
    src/gnss/stream_transport.h
    src/gnss/nmea_parser.h
    src/gnss/gnss_receiver.h
    src/tx/modulator.h
    src/tx/itx_backend.h
    src/tx/loopback_backend.h
    src/tx/tx_controller.h
    src/tx/soapy_tx_backend.h
    src/radio/ax25.h
    src/radio/kiss.h
    src/radio/radio_link.h
    src/radio/cat_client.h
    src/radio/cw_keyer.h
    src/ui/radio_panel.h
)

add_executable(mbdsdr ${SOURCES} ${HEADERS})
target_include_directories(mbdsdr PRIVATE src)

# Offscreen screenshot tool (reuses all sources except main.cpp).
set(SHOT_SRCS ${SOURCES})
list(REMOVE_ITEM SHOT_SRCS src/main.cpp)
add_executable(ui_shot_r27 tests/ui_screenshot_r14.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_r27 PRIVATE src build/ui_shot_r27_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_r27 PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Multi-VFO band-box screenshot (offscreen; not in ctest).
add_executable(ui_shot_mvfo tests/ui_screenshot_multi_vfo.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_mvfo PRIVATE src build/ui_shot_mvfo_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_mvfo PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Constellation + multi-VFO screenshot (offscreen; not in ctest).
add_executable(ui_shot_cst tests/ui_screenshot_constellation.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_cst PRIVATE src build/ui_shot_cst_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_cst PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Focus-mode / restrained-badge screenshot (offscreen; not in ctest).
add_executable(ui_shot_focus tests/ui_screenshot_focus.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_focus PRIVATE src build/ui_shot_focus_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_focus PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Measurement-features canvas screenshot: peak marker + noise floor + cursor
# readout, driven by deterministic harness frames (offscreen; not in ctest).
add_executable(ui_shot_measure tests/ui_screenshot_measure.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_measure PRIVATE src build/ui_shot_measure_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_measure PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

target_link_libraries(mbdsdr PRIVATE Qt6::Core Qt6::Gui Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent ${CMAKE_DL_LIBS})

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
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_integration PRIVATE src)
target_link_libraries(test_engine_integration PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_integration COMMAND test_engine_integration)

# IAudioSink contract: MemoryAudioSink capture/volume/mute + engine injection.
add_executable(test_audio_sink tests/test_audio_sink.cpp
    src/dsp/spectrum_engine.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_audio_sink PRIVATE src)
target_link_libraries(test_audio_sink PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME audio_sink COMMAND test_audio_sink)
set_tests_properties(audio_sink PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

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

add_executable(test_scanner tests/test_scanner.cpp src/dsp/frequency_scanner.cpp src/dsp/signal_watch.cpp)
target_include_directories(test_scanner PRIVATE src)
target_link_libraries(test_scanner PRIVATE Qt6::Test Qt6::Core)
add_test(NAME scanner COMMAND test_scanner)

add_executable(test_spectrum_interaction tests/test_spectrum_interaction.cpp
    src/ui/spectrum_widget.cpp src/ui/spectrum_display.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_spectrum_interaction PRIVATE src)
target_link_libraries(test_spectrum_interaction PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME spectrum_interaction COMMAND test_spectrum_interaction)

add_executable(test_spectrum_display tests/test_spectrum_display.cpp
    src/ui/spectrum_display.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_spectrum_display PRIVATE src)
target_link_libraries(test_spectrum_display PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME spectrum_display COMMAND test_spectrum_display)

# AI integration test: SKIPs without MBDSDR_AI_KEY env (never FAILs offline).
add_executable(test_ai_integration tests/test_ai_integration.cpp src/ai/llm_client.cpp src/ai/ai_config.cpp)
target_include_directories(test_ai_integration PRIVATE src)
target_link_libraries(test_ai_integration PRIVATE Qt6::Test Qt6::Core Qt6::Network)
add_test(NAME ai_integration COMMAND test_ai_integration)

# WAV roundtrip: record demod audio to disk, read header back.
add_executable(test_wav_roundtrip tests/test_wav_roundtrip.cpp
    src/dsp/spectrum_engine.cpp src/dsp/test_signal.cpp src/dsp/file_source.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_wav_roundtrip PRIVATE src)
target_link_libraries(test_wav_roundtrip PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME wav_roundtrip COMMAND test_wav_roundtrip)

add_executable(test_ai_function_calling tests/test_ai_function_calling.cpp
    src/dsp/spectrum_engine.cpp src/dsp/test_signal.cpp src/dsp/file_source.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/agent.cpp src/ai/agent_tools.cpp src/ai/ai_config.cpp)
target_include_directories(test_ai_function_calling PRIVATE src)
target_link_libraries(test_ai_function_calling PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME ai_function_calling COMMAND test_ai_function_calling)

# Shortcut smoke test: builds the full MainWindow (all sources, minus main.cpp).
set(TESTAPP_SRCS ${SOURCES})
list(REMOVE_ITEM TESTAPP_SRCS src/main.cpp)
add_executable(test_shortcuts tests/test_shortcuts.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_shortcuts PRIVATE src build/test_shortcuts_autogen/include)
target_link_libraries(test_shortcuts PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME shortcuts COMMAND test_shortcuts)

add_executable(test_geo tests/test_geo.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_geo PRIVATE src)
target_link_libraries(test_geo PRIVATE Qt6::Network Qt6::Concurrent Qt6::Core)
add_test(NAME geo COMMAND test_geo)

add_executable(test_pass_doppler tests/test_pass_doppler.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_pass_doppler PRIVATE src)
target_link_libraries(test_pass_doppler PRIVATE Qt6::Network Qt6::Concurrent Qt6::Core)
add_test(NAME pass_doppler COMMAND test_pass_doppler)

add_executable(test_adsb_cpr tests/test_adsb_cpr.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_adsb_cpr PRIVATE src)
target_link_libraries(test_adsb_cpr PRIVATE Qt6::Core)
add_test(NAME adsb_cpr COMMAND test_adsb_cpr)

add_executable(test_adsb_decode tests/test_adsb_decode.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_adsb_decode PRIVATE src)
target_link_libraries(test_adsb_decode PRIVATE Qt6::Core)
add_test(NAME adsb_decode COMMAND test_adsb_decode)

add_executable(test_aircraft_tracker tests/test_aircraft_tracker.cpp src/ui/aircraft_tracker.cpp)
target_include_directories(test_aircraft_tracker PRIVATE src)
# aircraft_tracker.h pulls in world_view.h (AircraftPoint), which needs QtWidgets.
target_link_libraries(test_aircraft_tracker PRIVATE Qt6::Core Qt6::Widgets)
add_test(NAME aircraft_tracker COMMAND test_aircraft_tracker)

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

# TX modulator -> demodulator loopback (AM/NFM/WFM/SSB/CW), not hardware.
add_executable(test_modulator tests/test_modulator.cpp
    src/tx/modulator.cpp src/dsp/demod.cpp)
target_include_directories(test_modulator PRIVATE src)
target_link_libraries(test_modulator PRIVATE Qt6::Core)
add_test(NAME modulator COMMAND test_modulator)

# AX.25/KISS TNC framing round-trip (not hardware).
add_executable(test_ax25 tests/test_ax25.cpp
    src/radio/ax25.cpp src/radio/kiss.cpp)
target_include_directories(test_ax25 PRIVATE src)
target_link_libraries(test_ax25 PRIVATE Qt6::Core)
add_test(NAME ax25 COMMAND test_ax25)

# TX interlock + PTT watchdog (not hardware).
add_executable(test_tx tests/test_tx.cpp
    src/tx/loopback_backend.cpp src/tx/tx_controller.cpp src/tx/modulator.cpp)
target_include_directories(test_tx PRIVATE src)
target_link_libraries(test_tx PRIVATE Qt6::Core)
add_test(NAME tx COMMAND test_tx)

# CAT (CI-V/Kenwood) + CW keyer (not hardware).
add_executable(test_radio tests/test_radio.cpp
    src/radio/cat_client.cpp src/radio/cw_keyer.cpp src/radio/radio_link.cpp)
target_include_directories(test_radio PRIVATE src)
target_link_libraries(test_radio PRIVATE Qt6::Core)
add_test(NAME radio COMMAND test_radio)

# SoapySDR TX backend honest error path (no Soapy required).
add_executable(test_soapy_tx tests/test_soapy_tx.cpp src/tx/soapy_tx_backend.cpp)
target_include_directories(test_soapy_tx PRIVATE src)
target_link_libraries(test_soapy_tx PRIVATE Qt6::Core ${CMAKE_DL_LIBS})
add_test(NAME soapy_tx COMMAND test_soapy_tx)

add_executable(test_recorder tests/test_recorder.cpp
    src/dsp/recorder.cpp src/dsp/file_source.cpp src/dsp/gated_recorder.cpp
    src/dsp/demod.cpp)
target_include_directories(test_recorder PRIVATE src)
target_link_libraries(test_recorder PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME recorder COMMAND test_recorder)

# Multi-VFO fan-out: two synthesized carriers decoded independently.
add_executable(test_multi_vfo tests/test_multi_vfo.cpp
    src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_multi_vfo PRIVATE src)
target_link_libraries(test_multi_vfo PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME multi_vfo COMMAND test_multi_vfo)

# Squelch gate + gated recorder linkage (filenames, no dead leading silence).
add_executable(test_squelch_gate tests/test_squelch_gate.cpp
    src/dsp/squelch.cpp src/dsp/gated_recorder.cpp)
target_include_directories(test_squelch_gate PRIVATE src)
target_link_libraries(test_squelch_gate PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME squelch_gate COMMAND test_squelch_gate)

# Unattended signal-triggered watch recording (detector + pre-roll + per-burst
# WAV/JSON sidecar; NOT HARDWARE, fully synthesized).
add_executable(test_watch_record tests/test_watch_record.cpp
    src/dsp/signal_watch.cpp src/dsp/gated_recorder.cpp src/dsp/squelch.cpp)
target_include_directories(test_watch_record PRIVATE src)
target_link_libraries(test_watch_record PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME watch_record COMMAND test_watch_record)
set_tests_properties(watch_record PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Bare-C++ digital demod test (BPSK/QPSK round-trip through Costas + diff codec).
add_executable(test_digital_demod tests/test_digital_demod.cpp
    src/dsp/digital_demod.cpp)
target_include_directories(test_digital_demod PRIVATE src)
target_link_libraries(test_digital_demod PRIVATE Qt6::Test)
add_test(NAME digital_demod COMMAND test_digital_demod)

# ANR: identity when off, measurable attenuation when on.
add_executable(test_anr tests/test_anr.cpp src/dsp/anr.cpp)
target_include_directories(test_anr PRIVATE src)
target_link_libraries(test_anr PRIVATE Qt6::Test)
add_test(NAME anr COMMAND test_anr)

# Constellation panel (QtTest + offscreen).
add_executable(test_constellation_view tests/test_constellation_view.cpp
    src/ui/constellation_view.cpp src/dsp/digital_demod.cpp)
target_include_directories(test_constellation_view PRIVATE src)
target_link_libraries(test_constellation_view PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui)
add_test(NAME constellation_view COMMAND test_constellation_view)

# Weather-satellite (NOAA APT) panel: empty state / setImage / clear round-trip
# (QtTest + offscreen). The QImage fed here is a hand-built test pattern,
# NOT HARDWARE -- the panel must never invent a cloud photo.
add_executable(test_weather_panel tests/test_weather_panel.cpp
    src/ui/weather_panel.cpp)
target_include_directories(test_weather_panel PRIVATE src)
target_link_libraries(test_weather_panel PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui)
add_test(NAME weather_panel COMMAND test_weather_panel)
set_tests_properties(weather_panel PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# End-to-end: TestSignalSource digital IQ -> real channelizer -> DigitalDemod lock.
add_executable(test_digital_e2e tests/test_digital_e2e.cpp
    src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp
    src/dsp/rds_decoder.cpp
    src/dsp/test_signal.cpp src/dsp/noise_blanker.cpp src/dsp/iq_frontend.cpp)
target_include_directories(test_digital_e2e PRIVATE src)
target_link_libraries(test_digital_e2e PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME digital_e2e COMMAND test_digital_e2e)

add_executable(test_decoder tests/test_decoder.cpp
    src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_decoder PRIVATE src)
target_link_libraries(test_decoder PRIVATE Qt6::Test Qt6::Core)
add_test(NAME decoder COMMAND test_decoder)

# RDS (EN 300 401) offline data-link decoder: reference encoder -> MPX -> decode.
add_executable(test_rds tests/test_rds.cpp
    src/dsp/rds_decoder.cpp src/dsp/demod.cpp)
target_include_directories(test_rds PRIVATE src)
target_link_libraries(test_rds PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rds COMMAND test_rds)

# RDS end-to-end: synthesized FM IQ -> DemodWFM MPX tap -> RdsDecoder.
add_executable(test_rds_chain tests/test_rds_chain.cpp
    src/dsp/rds_decoder.cpp src/dsp/demod.cpp)
target_include_directories(test_rds_chain PRIVATE src)
target_link_libraries(test_rds_chain PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rds_chain COMMAND test_rds_chain)

# WFM stereo offline: synthesized FM IQ -> DemodWFM raw MPX -> WfmStereoDecoder.
# No UI, no hardware; the composite MPX is built by hand in the test.
add_executable(test_wfm_stereo tests/test_wfm_stereo.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp)
target_include_directories(test_wfm_stereo PRIVATE src)
target_link_libraries(test_wfm_stereo PRIVATE Qt6::Test Qt6::Core)
add_test(NAME wfm_stereo COMMAND test_wfm_stereo)

# NOAA APT image decoder: synthetic test pattern -> 2400 Hz AM baseband ->
# streaming AptDecoder -> grayscale QImage. DSP core only, no UI.
add_executable(test_apt tests/test_apt.cpp src/dsp/apt_decoder.cpp src/dsp/demod.cpp)
target_include_directories(test_apt PRIVATE src)
target_link_libraries(test_apt PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME apt COMMAND test_apt)

add_executable(test_agent tests/test_agent.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/ai_config.cpp
    src/ai/agent_tools.cpp src/ai/agent.cpp src/dsp/spectrum_engine.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_agent PRIVATE src)
target_link_libraries(test_agent PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME agent COMMAND test_agent)

# GUI-dependent tests run on the offscreen platform so ctest works on
# headless/CI machines without a display.
set_tests_properties(recorder decoder agent PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# ---- GNSS / SGP4 / offline map / sky-view integration tests -------------
# NMEA 0183 parser: pure Qt Core, no event loop / GUI.
add_executable(test_nmea_parser tests/test_nmea_parser.cpp src/gnss/nmea_parser.cpp)
target_include_directories(test_nmea_parser PRIVATE src)
target_link_libraries(test_nmea_parser PRIVATE Qt6::Core)
add_test(NAME nmea_parser COMMAND test_nmea_parser)

# Transport backends (serial/file/memory) + GnssReceiver smoke test.
# GnssReceiver is a QThread (Q_OBJECT) -> needs AUTOMOC (global ON).
add_executable(test_transport tests/test_transport.cpp
    src/gnss/serial_transport.cpp src/gnss/stream_transport.cpp
    src/gnss/nmea_parser.cpp src/gnss/gnss_receiver.cpp)
target_include_directories(test_transport PRIVATE src)
target_link_libraries(test_transport PRIVATE Qt6::Core)
add_test(NAME transport COMMAND test_transport)

# Original near-earth SGP4 validated vs the published Vallado tforver.out set.
# No Qt dependency at all.
add_executable(test_sgp4 tests/test_sgp4.cpp src/dsp/sgp4.cpp)
target_include_directories(test_sgp4 PRIVATE src)
add_test(NAME sgp4 COMMAND test_sgp4)

# Offline world map + equirectangular projection GUI test (offscreen).
add_executable(test_map_view tests/test_map_view.cpp
    src/ui/world_view.cpp src/ui/map_projection.cpp)
target_include_directories(test_map_view PRIVATE src)
target_link_libraries(test_map_view PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME map_view COMMAND test_map_view)

# Polar sky view + elevation-vs-time plot GUI test (offscreen).
add_executable(test_sky_view tests/test_sky_view.cpp
    src/ui/sky_view.cpp src/ui/elevation_plot.cpp)
target_include_directories(test_sky_view PRIVATE src)
target_link_libraries(test_sky_view PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME sky_view COMMAND test_sky_view)

# New GUI tests must run headless (no display on CI / this build host).
set_tests_properties(map_view sky_view PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

message(STATUS "MBDSDR C++ build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)")

add_executable(ui_shot_wfm tests/ui_screenshot_wfm.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_wfm PRIVATE src build/ui_shot_wfm_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_wfm PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
