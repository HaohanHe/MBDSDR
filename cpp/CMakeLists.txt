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
    src/ui/s_meter.cpp
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
    src/dsp/device_capabilities.cpp
    src/dsp/fcch_detector.cpp
    src/dsp/frequency_calibrator.cpp
    src/dsp/spyserver_server.cpp
    src/ui/bookmark_manager.cpp
    src/ui/activity_log.cpp
    src/ui/shortcuts_dialog.cpp
    src/ui/calibration_dialog.cpp
    src/dsp/test_signal.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/device_lister.cpp
    src/dsp/device_presence_notifier.cpp
    src/dsp/spectrum_engine.cpp
    src/control/control_hub.cpp
    src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp
    src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp
    src/dsp/wfm_stereo.cpp
    src/dsp/digital_demod.cpp
    src/dsp/anr.cpp
    src/ui/constellation_view.cpp
    src/ui/weather_panel.cpp
    src/ui/pocsag_panel.cpp
    src/ui/m17_panel.cpp
    src/ui/vor_panel.cpp
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
    src/ai/ai_session_store.cpp
    src/ai/ai_context.cpp
    src/ai/tool_schema.cpp
    src/ai/arguments_validator.cpp
    src/ai/llm_protocol.cpp
    src/ai/task_orchestrator.cpp
    src/ai/task_runner.cpp
    src/ai/plan_parser.cpp
    src/ai/sat_task_planner.cpp
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
    src/ui/recording_library.cpp
    src/ui/task_steps_view.cpp
)

set(HEADERS
    src/core/tokens.h
    src/core/spectrum_frame.h
    src/core/bandwidth_preset.h
    src/ui/main_window.h
    src/ui/spectrum_widget.h
    src/ui/spectrum_display.h
    src/ui/calibration_dialog.h
    src/dsp/source.h
    src/dsp/device_capabilities.h
    src/dsp/fft.h
    src/dsp/iq_buffer.h
    src/dsp/power_spectrum.h
    src/dsp/test_signal.h
    src/dsp/rtl_sdr_source.h
    src/dsp/spyserver_server.h
    src/dsp/spectrum_engine.h
    src/control/control_hub.h
    src/dsp/iq_frontend.h
    src/dsp/channelizer.h
    src/dsp/audio_resampler.h
    src/dsp/demod.h
    src/dsp/wfm_stereo.h
    src/dsp/squelch.h
    src/dsp/agc.h
    src/dsp/frequency_calibrator.h
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
    src/ui/pocsag_panel.h
    src/ui/m17_panel.h
    src/ui/vor_panel.h
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
    src/ui/recording_library.h
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

# Upgraded multi-VFO management panel screenshot (offscreen; not in ctest).
add_executable(ui_shot_vfopanel tests/ui_screenshot_vfo_panel.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_vfopanel PRIVATE src build/ui_shot_vfopanel_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_vfopanel PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Narrow-window adaptation screenshot (offscreen; not in ctest).
add_executable(ui_shot_narrow tests/ui_screenshot_narrow.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_narrow PRIVATE src build/ui_shot_narrow_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_narrow PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Constellation + multi-VFO screenshot (offscreen; not in ctest).
add_executable(ui_shot_cst tests/ui_screenshot_constellation.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_cst PRIVATE src build/ui_shot_cst_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_cst PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Constellation WITH the new view controls: point density, one zoom notch, and
# the real I-histogram overlay toggled on (offscreen; not in ctest).
add_executable(ui_shot_cst2 tests/ui_screenshot_cst2.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_cst2 PRIVATE src build/ui_shot_cst2_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_cst2 PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Narrow (820px) re-check of the new constellation toolbar (offscreen; not in ctest).
add_executable(ui_shot_narrow2 tests/ui_screenshot_narrow2.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_narrow2 PRIVATE src build/ui_shot_narrow2_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_narrow2 PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# SpyServer remote-IQ control, enabled state (offscreen; not in ctest).
add_executable(ui_shot_spyserver tests/ui_screenshot_spyserver.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_spyserver PRIVATE src build/ui_shot_spyserver_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_spyserver PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Focus-mode / restrained-badge screenshot (offscreen; not in ctest).
add_executable(ui_shot_focus tests/ui_screenshot_focus.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_focus PRIVATE src build/ui_shot_focus_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_focus PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Measurement-features canvas screenshot: peak marker + noise floor + cursor
# readout, driven by deterministic harness frames (offscreen; not in ctest).
add_executable(ui_shot_measure tests/ui_screenshot_measure.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_measure PRIVATE src build/ui_shot_measure_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_measure PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Hotplug event-channel screenshot: real loopback rtl_tcp server drives the
# engine through connect -> drop (设备断开) -> auto-reconnect (recovered).
add_executable(ui_shot_hotplug tests/ui_screenshot_hotplug.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_hotplug PRIVATE src build/ui_shot_hotplug_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_hotplug PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Recording-library panel screenshot (real WAV+sidecar in a throwaway dir, live
# watch status). Offscreen; not in ctest.
add_executable(ui_shot_reclib tests/ui_screenshot_rec_library.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_reclib PRIVATE src build/ui_shot_reclib_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_reclib PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Enhanced status-strip screenshot (RSSI/SNR/squelch/GNSS readouts). Offscreen.
add_executable(ui_shot_statusstrip tests/ui_screenshot_status_strip.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_statusstrip PRIVATE src build/ui_shot_statusstrip_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_statusstrip PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Offline file-analysis panel: a real WAV streamed through the engine DSP chain.
# Offscreen; not in ctest. Writes cpp/scratch/ui_file_analysis.png.
add_executable(ui_shot_fileanalysis tests/ui_screenshot_file_analysis.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_fileanalysis PRIVATE src build/ui_shot_fileanalysis_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_fileanalysis PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Sky time-scrubber preview + trajectory, GIS legend + GNSS fix, clock-domain readout.
# Offscreen; not in ctest. Writes cpp/scratch/ui_sky_drag.png etc.
add_executable(ui_shot_skytime tests/ui_screenshot_sky_time.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_skytime PRIVATE src build/ui_shot_skytime_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_skytime PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Timing-panel three-state + visible-nav-satellites (prediction) screenshots.
# Offscreen; not in ctest. Writes cpp/scratch/ui_timing_panel.png / ui_nav_sats.png.
add_executable(ui_shot_timing tests/ui_screenshot_timing.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_timing PRIVATE src build/ui_shot_timing_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_timing PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# LEO geometry readout + S-meter screenshots. Offscreen; not in ctest.
add_executable(ui_shot_geo tests/ui_screenshot_geo.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_geo PRIVATE src build/ui_shot_geo_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_geo PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# TLE freshness panel screenshot. Offscreen; not in ctest.
add_executable(ui_shot_tle tests/ui_screenshot_tle.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_tle PRIVATE src build/ui_shot_tle_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_tle PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Device-info panel + dynamic sample-rate combo (real loopback RTL0 handshake).
# Offscreen; not in ctest. Writes cpp/scratch/ui_devices.png + ui_devices_empty.png.
add_executable(ui_shot_devices tests/ui_screenshot_devices.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_devices PRIVATE src build/ui_shot_devices_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_devices PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

add_executable(ui_shot_audit tests/ui_screenshot_audit.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_audit PRIVATE src build/ui_shot_audit_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_audit PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

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
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_integration PRIVATE src)
target_link_libraries(test_engine_integration PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_integration COMMAND test_engine_integration)

# Phase21 Step1: honest empty state. No hardware + no explicit opt-in -> the
# engine installs a NullSource, produces no IQ (hasData()=false, no
# spectrumReady), and never self-synthesizes. Only setTestSourceEnabled(true) /
# the --test-source CLI flag / MBDSDR_TEST_SOURCE=1 brings up the synthetic
# source (isSynthetic()=true). Mirrors the engine_integration source set.
add_executable(test_engine_empty_state tests/test_engine_empty_state.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_empty_state PRIVATE src)
target_link_libraries(test_engine_empty_state PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_empty_state COMMAND test_engine_empty_state)

# Headless ControlHub (GUI-decoupled command layer). Drives the REAL engine via
# named commands with NO QWidget: deterministic write->readback, the read/write
# gate in both states, honest unknown/bad-arg errors, and the no-engine / no-
# telemetry empty states. Synthetic TestSignalSource only; recording case starts
# the run loop briefly (mirrors test_wav_roundtrip).
add_executable(test_control_hub tests/test_control_hub.cpp
    src/control/control_hub.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_control_hub PRIVATE src)
target_link_libraries(test_control_hub PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME control_hub COMMAND test_control_hub)

# Phase22-A remote/threading hardening: (1) foreign worker threads hammer
# execute() concurrently -> the engine dispatch is marshaled to the engine home
# thread (BlockingQueued), no crash / no data race; (2) get_status connected/
# status/error_message flip honestly across a real TCP-loopback connect -> drop ->
# auto-reconnect, plus a connect-failure reason. Synthetic offline fallback only.
add_executable(test_control_hub_remote tests/test_control_hub_remote.cpp
    src/control/control_hub.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_control_hub_remote PRIVATE src)
target_link_libraries(test_control_hub_remote PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME control_hub_remote COMMAND test_control_hub_remote)

# P1: deterministic synthetic IQ -> FileSource.openRaw -> REAL engine ->
# MemoryAudioSink. Asserts recovered 1 kHz pitch / level for NFM/WFM/AM, the
# quiet-noise control, squelch OPEN/CLOSED, and no rational-resampler pitch drift.
add_executable(test_engine_audio_e2e tests/test_engine_audio_e2e.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_audio_e2e PRIVATE src)
target_link_libraries(test_engine_audio_e2e PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_audio_e2e COMMAND test_engine_audio_e2e)

# B3/B4: per-mode bandwidth table + coverage rule + engine real setBandwidth +
# PowerSpectrum frame-average lock.
add_executable(test_bandwidth_preset tests/test_bandwidth_preset.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_bandwidth_preset PRIVATE src)
target_link_libraries(test_bandwidth_preset PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME bandwidth_preset COMMAND test_bandwidth_preset)

add_executable(test_decimation tests/test_decimation.cpp
    src/dsp/channelizer.cpp src/dsp/fft.cpp)
target_include_directories(test_decimation PRIVATE src)
target_link_libraries(test_decimation PRIVATE Qt6::Test Qt6::Core)
add_test(NAME decimation COMMAND test_decimation)

# Device hotplug event channel: drop detection, honest fallback, 2 s rtl_tcp
# auto-reconnect, connect-failure reason (real TCP loopback, no mocks).
add_executable(test_engine_hotplug tests/test_engine_hotplug.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_engine_hotplug PRIVATE src)
target_link_libraries(test_engine_hotplug PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME engine_hotplug COMMAND test_engine_hotplug)

# SpyServer (SDR++/Airspy) server: real loopback TCP self-connect over the
# documented wire format -- handshake, SET_SETTING, real IQ push, stop-keep-alive.
add_executable(test_spyserver_server tests/test_spyserver_server.cpp
    src/dsp/spyserver_server.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_spyserver_server PRIVATE src)
target_link_libraries(test_spyserver_server PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME spyserver_server COMMAND test_spyserver_server)

# Offscreen end-to-end smoke over a POSIX loopback rtl_tcp (NOT HARDWARE):
# handshake + deterministic tone IQ -> engine frames flow, spectrum lights up,
# settings read back, illegal freq/rate honestly ignored.
add_executable(test_e2e_smoke tests/test_e2e_smoke.cpp
    src/dsp/spyserver_server.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/dsp/fcch_detector.cpp)
target_include_directories(test_e2e_smoke PRIVATE src)
target_link_libraries(test_e2e_smoke PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME e2e_smoke COMMAND test_e2e_smoke)

# IAudioSink contract: MemoryAudioSink capture/volume/mute + engine injection.
add_executable(test_audio_sink tests/test_audio_sink.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
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

# dB axis auto-range + drag-tune/waterfall alignment contract (offscreen).
add_executable(test_spectrum_autorange tests/test_spectrum_autorange.cpp
    src/ui/spectrum_display.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_spectrum_autorange PRIVATE src)
target_link_libraries(test_spectrum_autorange PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME spectrum_autorange COMMAND test_spectrum_autorange)

# AI integration test: SKIPs without MBDSDR_AI_KEY env (never FAILs offline).
# llm_client.cpp now calls the M3 protocol layer and includes core/tokens.h
# (which pulls QtGui), so link llm_protocol.cpp + Qt6::Gui here.
add_executable(test_ai_integration tests/test_ai_integration.cpp
    src/ai/llm_client.cpp src/ai/llm_protocol.cpp src/ai/ai_config.cpp)
target_include_directories(test_ai_integration PRIVATE src)
target_link_libraries(test_ai_integration PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Gui)
add_test(NAME ai_integration COMMAND test_ai_integration)

# WAV roundtrip: record demod audio to disk, read header back.
add_executable(test_wav_roundtrip tests/test_wav_roundtrip.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/file_source.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_wav_roundtrip PRIVATE src)
target_link_libraries(test_wav_roundtrip PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME wav_roundtrip COMMAND test_wav_roundtrip)

add_executable(test_ai_function_calling tests/test_ai_function_calling.cpp
    src/dsp/frequency_calibrator.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/file_source.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/agent.cpp src/ai/agent_tools.cpp src/ai/ai_config.cpp src/ai/ai_context.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ui/bookmark_manager.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp)
target_include_directories(test_ai_function_calling PRIVATE src)
target_link_libraries(test_ai_function_calling PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
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

# Visible navigation satellites (prediction): constellation filter, catalog
# number parse, and MEO-scale propagation through the shared SGP4 path.
add_executable(test_nav_sats tests/test_nav_sats.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_nav_sats PRIVATE src)
target_link_libraries(test_nav_sats PRIVATE Qt6::Test Qt6::Network Qt6::Concurrent Qt6::Core)
add_test(NAME nav_sats COMMAND test_nav_sats)

# TLE freshness: loopback HTTP pull -> cache w/ timestamp, epoch parse, offline
# cache->builtin fallback, no duplicate per-group requests.
add_executable(test_tle_fetch tests/test_tle_fetch.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_tle_fetch PRIVATE src)
target_link_libraries(test_tle_fetch PRIVATE Qt6::Test Qt6::Network Qt6::Concurrent Qt6::Core)
add_test(NAME tle_fetch COMMAND test_tle_fetch)

# LEO PNT geometry availability (pure, prediction-only) and S-meter mapping.
add_executable(test_pnt_geometry tests/test_pnt_geometry.cpp)
target_include_directories(test_pnt_geometry PRIVATE src)
target_link_libraries(test_pnt_geometry PRIVATE Qt6::Test Qt6::Gui Qt6::Core)
add_test(NAME pnt_geometry COMMAND test_pnt_geometry)

add_executable(test_s_meter tests/test_s_meter.cpp src/ui/s_meter.cpp)
target_include_directories(test_s_meter PRIVATE src)
target_link_libraries(test_s_meter PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME s_meter COMMAND test_s_meter)

# Pre-hardware loopback soak: connect->record->offline file replay (fixed chunks)
# -> engine still healthy -> LEO geometry + S-meter over measured numbers.
add_executable(test_leo_soak tests/test_leo_soak.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_leo_soak PRIVATE src build/test_leo_soak_autogen/include)
target_link_libraries(test_leo_soak PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME leo_soak COMMAND test_leo_soak)

# Satellite one-tap capture + Doppler auto-compensation decision logic
# (mapping table, capture target, bounded step limiter, real SGP4 -> fd -> VFO).
add_executable(test_sat_capture tests/test_sat_capture.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_sat_capture PRIVATE src)
target_link_libraries(test_sat_capture PRIVATE Qt6::Network Qt6::Concurrent Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME sat_capture COMMAND test_sat_capture)

add_executable(test_adsb_cpr tests/test_adsb_cpr.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_adsb_cpr PRIVATE src)
target_link_libraries(test_adsb_cpr PRIVATE Qt6::Core)
add_test(NAME adsb_cpr COMMAND test_adsb_cpr)

add_executable(test_adsb_decode tests/test_adsb_decode.cpp src/dsp/adsb_decoder.cpp)
target_include_directories(test_adsb_decode PRIVATE src)
target_link_libraries(test_adsb_decode PRIVATE Qt6::Core)
add_test(NAME adsb_decode COMMAND test_adsb_decode)

# SSDV byte layer (packet header parse + RS(255,223) CCSDS encoder)
# and SSTV pure functions (VIS decode, zero-crossing inst freq). No Qt deps.
add_executable(test_ssdv_packet tests/test_ssdv_packet.cpp src/dsp/ssdv_packet.cpp)
target_include_directories(test_ssdv_packet PRIVATE src)
target_link_libraries(test_ssdv_packet PRIVATE Qt6::Core)
add_test(NAME ssdv_packet COMMAND test_ssdv_packet)

add_executable(test_sstv_vis tests/test_sstv_vis.cpp src/dsp/sstv_vis.cpp)
target_include_directories(test_sstv_vis PRIVATE src)
target_link_libraries(test_sstv_vis PRIVATE Qt6::Core)
add_test(NAME sstv_vis COMMAND test_sstv_vis)

# Phase 17: POCSAG paging / m17 / VOR navigation digital decoders.
# POCSAG and m17 tests are plain main() (Qt-free cores); VOR uses QtTest.
add_executable(test_pocsag_decoder tests/test_pocsag_decoder.cpp src/dsp/pocsag_decoder.cpp)
target_include_directories(test_pocsag_decoder PRIVATE src)
target_link_libraries(test_pocsag_decoder PRIVATE Qt6::Core)
add_test(NAME pocsag_decoder COMMAND test_pocsag_decoder)

add_executable(test_m17_decoder tests/test_m17_decoder.cpp src/dsp/m17_decoder.cpp)
target_include_directories(test_m17_decoder PRIVATE src)
target_link_libraries(test_m17_decoder PRIVATE Qt6::Core)
add_test(NAME m17_decoder COMMAND test_m17_decoder)

add_executable(test_vor_receiver tests/test_vor_receiver.cpp src/dsp/vor_receiver.cpp)
target_include_directories(test_vor_receiver PRIVATE src)
target_link_libraries(test_vor_receiver PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME vor_receiver COMMAND test_vor_receiver)

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
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/fft.cpp)
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
    src/tx/modulator.cpp src/dsp/demod.cpp src/dsp/agc.cpp)
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
    src/dsp/device_capabilities.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp)
target_include_directories(test_recorder PRIVATE src)
target_link_libraries(test_recorder PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME recorder COMMAND test_recorder)

# Multi-VFO fan-out: two synthesized carriers decoded independently.
add_executable(test_multi_vfo tests/test_multi_vfo.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_multi_vfo PRIVATE src)
target_link_libraries(test_multi_vfo PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME multi_vfo COMMAND test_multi_vfo)

# Phase18 Wave1: POCSAG / m17 / VOR closed-loop through the VfoManager chain.
# Synthetic IQ only (NOT HARDWARE): channelizer -> front-end -> decoder ->
# read-only snapshot assertions, plus mode-switch-clears and noise empty-state.
add_executable(test_digital_link_integration tests/test_digital_link_integration.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_digital_link_integration PRIVATE src)
target_link_libraries(test_digital_link_integration PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME digital_link_integration COMMAND test_digital_link_integration)

# Phase24 Block3: scanner <-> schedule linkage closed loop (dsp-internal).
# Synthetic RSSI table + synthetic 2-FSK burst ONLY (NOT HARDWARE):
# FrequencyScanner sweep -> onRetune -> onActivityFound(park+POCSAG decode+arm
# Recorder) -> onDwellEnded(stop recorder). Asserts the full scan->hit->dwell->
# decode->record chain on the busy channel, and the honest empty state on a
# quiet band (no self-trigger).
add_executable(test_scan_link tests/test_scan_link.cpp
    src/dsp/scan_link.cpp src/dsp/frequency_scanner.cpp src/dsp/signal_watch.cpp
    src/dsp/recorder.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_scan_link PRIVATE src)
target_link_libraries(test_scan_link PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME scan_link COMMAND test_scan_link)

# Squelch gate + gated recorder linkage (filenames, no dead leading silence).
add_executable(test_squelch_gate tests/test_squelch_gate.cpp
    src/dsp/squelch.cpp src/dsp/gated_recorder.cpp)
target_include_directories(test_squelch_gate PRIVATE src)
target_link_libraries(test_squelch_gate PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME squelch_gate COMMAND test_squelch_gate)

# Audio-RMS-domain squelch auto-gate backing: real tracked noise floor signal +
# the proven gate mute path. Engine-only, offline test source.
add_executable(test_squelch_autogate tests/test_squelch_autogate.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_squelch_autogate PRIVATE src)
target_link_libraries(test_squelch_autogate PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME squelch_autogate COMMAND test_squelch_autogate)
set_tests_properties(squelch_autogate PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Offscreen MainWindow integration: AI manual gate annotation, scan-hit save
# bookmark + jump, squelch auto-gate. Builds the full app minus main.cpp.
add_executable(test_ui_integration tests/test_ui_integration.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_ui_integration PRIVATE src build/test_ui_integration_autogen/include)
target_link_libraries(test_ui_integration PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME ui_integration COMMAND test_ui_integration)
set_tests_properties(ui_integration PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Device capability readback: rtl_tcp RTL0 handshake -> real tuner identity /
# driver-known ranges, dynamic sample-rate options, honest no-device state.
add_executable(test_device_capabilities tests/test_device_capabilities.cpp
    src/dsp/device_capabilities.cpp)
target_include_directories(test_device_capabilities PRIVATE src)
target_link_libraries(test_device_capabilities PRIVATE Qt6::Test Qt6::Core)
add_test(NAME device_capabilities COMMAND test_device_capabilities)

# Tuner discrete-gain snapping (no hardware): nearest-step snap, out-of-range
# clamp, half-step tie, empty-table honest passthrough, readback shape.
add_executable(test_tuner_gain_table tests/test_tuner_gain_table.cpp
    src/dsp/tuner_gain_table.cpp)
target_include_directories(test_tuner_gain_table PRIVATE src)
target_link_libraries(test_tuner_gain_table PRIVATE Qt6::Test Qt6::Core)
add_test(NAME tuner_gain_table COMMAND test_tuner_gain_table)

# Read-stream liveness watchdog (no hardware): failure counting, threshold
# latch, successful-read reset, latched-dead until reset, reconnect recovery.
add_executable(test_stream_watchdog tests/test_stream_watchdog.cpp
    src/dsp/stream_watchdog.cpp)
target_include_directories(test_stream_watchdog PRIVATE src)
target_link_libraries(test_stream_watchdog PRIVATE Qt6::Test Qt6::Core)
add_test(NAME stream_watchdog COMMAND test_stream_watchdog)

# Pull-based device enumeration diff (no hardware, no libusb hotplug): baseline,
# added, removed, unchanged, rename=remove+add, empty-to-empty.
add_executable(test_device_lister tests/test_device_lister.cpp
    src/dsp/device_lister.cpp)
target_include_directories(test_device_lister PRIVATE src)
target_link_libraries(test_device_lister PRIVATE Qt6::Test Qt6::Core)
add_test(NAME device_lister COMMAND test_device_lister)

# Hot-plug UI notice seam (no hardware): enumeration diff -> exact notice text
# ("RTL-SDR 已连接/已移除：…"), baseline/unchanged stays silent, kind flag.
add_executable(test_device_presence_notifier tests/test_device_presence_notifier.cpp
    src/dsp/device_lister.cpp src/dsp/device_presence_notifier.cpp)
target_include_directories(test_device_presence_notifier PRIVATE src)
target_link_libraries(test_device_presence_notifier PRIVATE Qt6::Test Qt6::Core)
add_test(NAME device_presence_notifier COMMAND test_device_presence_notifier)

# Sound-card link health state machine (no audio device): underrun warning,
# fatal-error dead latch, bounded/throttled reconnect, give-up, rebuild reset.
# Header-only (audio_link_health.h).
add_executable(test_audio_link_health tests/test_audio_link_health.cpp)
target_include_directories(test_audio_link_health PRIVATE src)
target_link_libraries(test_audio_link_health PRIVATE Qt6::Test Qt6::Core)
add_test(NAME audio_link_health COMMAND test_audio_link_health)

# Discrete gain-control decision (no radio): table->combo, empty->continuous
# slider with honest reason, no-hardware disabled.
add_executable(test_gain_control_model tests/test_gain_control_model.cpp)
target_include_directories(test_gain_control_model PRIVATE src)
target_link_libraries(test_gain_control_model PRIVATE Qt6::Test Qt6::Core)
add_test(NAME gain_control_model COMMAND test_gain_control_model)

# "Currently-audible VFO" contract (synthetic wideband): process() returns the
# SELECTED channel's audio, exactly one marker selected, switching flips both.
add_executable(test_vfo_audible tests/test_vfo_audible.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp src/dsp/rds_decoder.cpp)
target_include_directories(test_vfo_audible PRIVATE src)
target_link_libraries(test_vfo_audible PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME vfo_audible COMMAND test_vfo_audible)

# Offline GSM FCCH detector + clock-ppm estimator (NOT HARDWARE): synthetic
# tone+noise IQ recovers the tone and the injected ppm; noise-only / too-short
# honestly report not-detected.
add_executable(test_fcch tests/test_fcch.cpp src/dsp/fcch_detector.cpp)
target_include_directories(test_fcch PRIVATE src)
target_link_libraries(test_fcch PRIVATE Qt6::Test Qt6::Core)
add_test(NAME fcch COMMAND test_fcch)

# Frequency calibrator (晶振 ppm 自动测量, pure offline). Synthetic clock-error
# fixtures (synthetic test fixture, NOT real reception): handheld/manual/FCCH
# paths recover an injected +32/-20 ppm to sub-ppm, the post-apply residual is
# ~0, and pure noise / a buried tone honestly report no reference carrier.
add_executable(test_frequency_calibrator tests/test_frequency_calibrator.cpp src/dsp/frequency_calibrator.cpp)
target_include_directories(test_frequency_calibrator PRIVATE src)
target_link_libraries(test_frequency_calibrator PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME frequency_calibrator COMMAND test_frequency_calibrator)

# Calibration wizard (self-contained QDialog). Driven headless through an
# injected synthetic capture provider: steps through every page, locks the
# reference, reports delta/ppm, applies + shows the before/after comparison, and
# honestly shows the no-provider / no-signal empty state. Offscreen.
add_executable(test_calibration_dialog tests/test_calibration_dialog.cpp
    src/ui/calibration_dialog.cpp src/dsp/frequency_calibrator.cpp)
target_include_directories(test_calibration_dialog PRIVATE src
    build/test_calibration_dialog_autogen/include)
target_link_libraries(test_calibration_dialog PRIVATE
    Qt6::Test Qt6::Widgets Qt6::Core Qt6::Gui)
add_test(NAME calibration_dialog COMMAND test_calibration_dialog)
set_tests_properties(calibration_dialog PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Offscreen device-info panel + dynamic sample-rate combo: honest empty state,
# real loopback RTL0 handshake -> populated combo, device pull -> back to empty.
add_executable(test_device_ui tests/test_device_ui.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_device_ui PRIVATE src build/test_device_ui_autogen/include)
target_link_libraries(test_device_ui PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME device_ui COMMAND test_device_ui)
set_tests_properties(device_ui PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Phase 21 Step 2: honest no-hardware empty state + explicit opt-in synthetic
# test source + first-run onboarding card. Cloud VM has no RTL-SDR, so the
# engine defaults to the empty NullSource; the synthetic source appears ONLY
# when the user actively picks the combo item. Offscreen, no hardware.
add_executable(test_phase21_empty_state tests/test_phase21_empty_state.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_phase21_empty_state PRIVATE src build/test_phase21_empty_state_autogen/include)
target_link_libraries(test_phase21_empty_state PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME phase21_empty_state COMMAND test_phase21_empty_state)
set_tests_properties(phase21_empty_state PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

add_executable(test_ui_audit tests/test_ui_audit.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_ui_audit PRIVATE src build/test_ui_audit_autogen/include)
target_link_libraries(test_ui_audit PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME ui_audit COMMAND test_ui_audit)
set_tests_properties(ui_audit PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

add_executable(test_soak tests/test_soak.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_soak PRIVATE src build/test_soak_autogen/include)
target_link_libraries(test_soak PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME soak COMMAND test_soak)
set_tests_properties(soak PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

add_executable(test_fixed_marker tests/test_fixed_marker.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_fixed_marker PRIVATE src build/test_fixed_marker_autogen/include)
target_link_libraries(test_fixed_marker PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME fixed_marker COMMAND test_fixed_marker)
set_tests_properties(fixed_marker PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Unattended signal-triggered watch recording (detector + pre-roll + per-burst
# WAV/JSON sidecar; NOT HARDWARE, fully synthesized).
add_executable(test_watch_record tests/test_watch_record.cpp
    src/dsp/signal_watch.cpp src/dsp/gated_recorder.cpp src/dsp/squelch.cpp)
target_include_directories(test_watch_record PRIVATE src)
target_link_libraries(test_watch_record PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME watch_record COMMAND test_watch_record)
set_tests_properties(watch_record PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Recording-library core: scan real dir, sidecar metadata, empty state, delete,
# WAV header probe, filename fallback. Pure QtCore -- no hardware / no audio.
add_executable(test_recording_library tests/test_recording_library.cpp
    src/ui/recording_library.cpp)
target_include_directories(test_recording_library PRIVATE src)
target_link_libraries(test_recording_library PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME recording_library COMMAND test_recording_library)
set_tests_properties(recording_library PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# Offline file analysis: streaming FileSource (WAV/SigMF/raw, chunked, seek,
# pause, honest errors) + engine openOfflineFile path.
add_executable(test_file_source tests/test_file_source.cpp
    src/dsp/file_source.cpp src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_file_source PRIVATE src)
target_link_libraries(test_file_source PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME file_source COMMAND test_file_source)
set_tests_properties(file_source PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

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

# Phase18 Wave2: POCSAG / m17 / VOR decoder panels. Feeds the SAME snapshot shapes
# the engine pushes (pocsagMessagesChanged / m17CallsChanged / vorRadialChanged)
# straight into the panel slots and asserts the honest display contract:
# synthetic frames render correctly, an empty list / unlocked VorResult is the
# honest empty state (no fabricated message / callsign / bearing), clear() emits
# clearRequested, and a narrow resize renders without overflow. NOT HARDWARE.
add_executable(test_digital_panel_ui tests/test_digital_panel_ui.cpp
    src/ui/pocsag_panel.cpp src/ui/m17_panel.cpp src/ui/vor_panel.cpp)
target_include_directories(test_digital_panel_ui PRIVATE src)
target_link_libraries(test_digital_panel_ui PRIVATE Qt6::Test Qt6::Widgets Qt6::Gui Qt6::Core)
add_test(NAME digital_panel_ui COMMAND test_digital_panel_ui)
set_tests_properties(digital_panel_ui PROPERTIES ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

# End-to-end: TestSignalSource digital IQ -> real channelizer -> DigitalDemod lock.
add_executable(test_digital_e2e tests/test_digital_e2e.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp
    src/dsp/rds_decoder.cpp
    src/dsp/test_signal.cpp src/dsp/noise_blanker.cpp src/dsp/iq_frontend.cpp
    src/dsp/device_capabilities.cpp)
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
    src/dsp/rds_decoder.cpp src/dsp/demod.cpp src/dsp/agc.cpp)
target_include_directories(test_rds PRIVATE src)
target_link_libraries(test_rds PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rds COMMAND test_rds)

# RDS end-to-end: synthesized FM IQ -> DemodWFM MPX tap -> RdsDecoder.
add_executable(test_rds_chain tests/test_rds_chain.cpp
    src/dsp/rds_decoder.cpp src/dsp/demod.cpp src/dsp/agc.cpp)
target_include_directories(test_rds_chain PRIVATE src)
target_link_libraries(test_rds_chain PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rds_chain COMMAND test_rds_chain)

# WFM stereo offline: synthesized FM IQ -> DemodWFM raw MPX -> WfmStereoDecoder.
# No UI, no hardware; the composite MPX is built by hand in the test.
add_executable(test_wfm_stereo tests/test_wfm_stereo.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/wfm_stereo.cpp)
target_include_directories(test_wfm_stereo PRIVATE src)
target_link_libraries(test_wfm_stereo PRIVATE Qt6::Test Qt6::Core)
add_test(NAME wfm_stereo COMMAND test_wfm_stereo)

# NOAA APT image decoder: synthetic test pattern -> 2400 Hz AM baseband ->
# streaming AptDecoder -> grayscale QImage. DSP core only, no UI.
add_executable(test_apt tests/test_apt.cpp src/dsp/apt_decoder.cpp src/dsp/demod.cpp src/dsp/agc.cpp)
target_include_directories(test_apt PRIVATE src)
target_link_libraries(test_apt PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME apt COMMAND test_apt)

add_executable(test_agent tests/test_agent.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/ai_config.cpp
    src/ai/agent_tools.cpp src/ai/agent.cpp src/ai/ai_context.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ui/bookmark_manager.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_agent PRIVATE src)
target_link_libraries(test_agent PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME agent COMMAND test_agent)

# Autonomous multi-step task orchestrator: real engine through deterministic
# templates (sweep->hit->bookmark/record), interrupt, max-steps, gate report.
add_executable(test_task_orchestrator tests/test_task_orchestrator.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/task_orchestrator.cpp src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/ai_config.cpp
    src/ai/agent_tools.cpp src/ai/ai_context.cpp src/ui/bookmark_manager.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/sat_task_planner.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_task_orchestrator PRIVATE src)
target_link_libraries(test_task_orchestrator PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME task_orchestrator COMMAND test_task_orchestrator)

# LLM plan parser: trustworthy parse + honest rejection/fallback.
add_executable(test_plan_parser tests/test_plan_parser.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/plan_parser.cpp src/ai/task_orchestrator.cpp src/ai/llm_client.cpp
    src/ai/llm_worker.cpp src/ai/ai_config.cpp src/ai/agent_tools.cpp src/ai/ai_context.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/ui/bookmark_manager.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/sat_task_planner.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_plan_parser PRIVATE src)
target_link_libraries(test_plan_parser PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Gui Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME plan_parser COMMAND test_plan_parser)

# Satellite auto-tune task planner: real offline SGP4 -> capture frequency.
add_executable(test_sat_task_planner tests/test_sat_task_planner.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ai/llm_client.cpp
    src/ai/llm_worker.cpp src/ai/ai_config.cpp src/ai/agent_tools.cpp src/ai/ai_context.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp
    src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp
    src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp
    src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ui/bookmark_manager.cpp)
target_include_directories(test_sat_task_planner PRIVATE src)
target_link_libraries(test_sat_task_planner PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME sat_task_planner COMMAND test_sat_task_planner)

# Async task runner: worker QThread keeps UI responsive, queued steps, stop.
add_executable(test_task_async tests/test_task_async.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/task_runner.cpp src/ai/task_orchestrator.cpp src/ai/llm_client.cpp src/ai/llm_worker.cpp
    src/ai/ai_config.cpp src/ai/agent_tools.cpp src/ai/ai_context.cpp src/ui/bookmark_manager.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp
    src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/sat_task_planner.cpp src/dsp/tle_client.cpp src/dsp/sgp4.cpp)
target_include_directories(test_task_async PRIVATE src)
target_link_libraries(test_task_async PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME task_async COMMAND test_task_async)

# Tuner AGC: rtl_tcp real 0x03 command byte + engine readback.
add_executable(test_agc tests/test_agc.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/iq_frontend.cpp
    src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp
    src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp
    src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp
    src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_agc PRIVATE src)
target_link_libraries(test_agc PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME agc COMMAND test_agc)

# Signal activity log: real scan result -> entry, export/clear/persist, separate
# from bookmarks.
add_executable(test_activity_log tests/test_activity_log.cpp
    src/ui/activity_log.cpp src/ui/bookmark_manager.cpp)
target_include_directories(test_activity_log PRIVATE src)
target_link_libraries(test_activity_log PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets)
add_test(NAME activity_log COMMAND test_activity_log)

# Recorder naming: same-second captures do not overwrite each other.
add_executable(test_recorder_naming tests/test_recorder_naming.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp)
target_include_directories(test_recorder_naming PRIVATE src)
target_link_libraries(test_recorder_naming PRIVATE Qt6::Test Qt6::Core Qt6::Widgets)
add_test(NAME recorder_naming COMMAND test_recorder_naming)

# AI multi-session store (CRUD + JSON persistence) + pure context compaction.
# No network / no GUI: links only the two new AI translation units.
add_executable(test_ai_session tests/test_ai_session.cpp
    src/ai/ai_session_store.cpp src/ai/ai_context.cpp)
target_include_directories(test_ai_session PRIVATE src)
target_link_libraries(test_ai_session PRIVATE Qt6::Test Qt6::Core Qt6::Network)
add_test(NAME ai_session COMMAND test_ai_session)

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

# Clock-domain Δt = GNSS − system (ms), pure header-only helper + throttle tokens.
add_executable(test_clock_domain tests/test_clock_domain.cpp)
target_include_directories(test_clock_domain PRIVATE src)
target_link_libraries(test_clock_domain PRIVATE Qt6::Test Qt6::Gui Qt6::Core)
add_test(NAME clock_domain COMMAND test_clock_domain)

# Sky "now/preview" time scrubber: drag moves the sky clock to wall-now+offset
# (throttled), release returns to live. Builds the full app (offscreen).
add_executable(test_sky_time_slider tests/test_sky_time_slider.cpp ${TESTAPP_SRCS} ${HEADERS})
target_include_directories(test_sky_time_slider PRIVATE src build/test_sky_time_slider_autogen/include)
target_link_libraries(test_sky_time_slider PRIVATE Qt6::Test Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)
add_test(NAME sky_time_slider COMMAND test_sky_time_slider)

# New GUI tests must run headless (no display on CI / this build host).
set_tests_properties(map_view sky_view sky_time_slider PROPERTIES
    ENVIRONMENT "QT_QPA_PLATFORM=offscreen")

message(STATUS "MBDSDR C++ build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Qt6 version: ${Qt6Core_VERSION}")
message(STATUS "Target: mbdsdr (dynamic Qt6 linking -- system Qt6 is shared)")

add_executable(ui_shot_wfm tests/ui_screenshot_wfm.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_wfm PRIVATE src build/ui_shot_wfm_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_wfm PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Squelch auto-gate offscreen screenshot (not in ctest).
add_executable(ui_shot_sqlauto tests/ui_screenshot_squelch_auto.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_sqlauto PRIVATE src build/ui_shot_sqlauto_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_sqlauto PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Scan-hit save-bookmark offscreen screenshot (not in ctest).
add_executable(ui_shot_scanbm tests/ui_screenshot_scan_bm.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_scanbm PRIVATE src build/ui_shot_scanbm_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_scanbm PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# Satellite one-tap capture + Doppler lock offscreen screenshot (not in ctest).
add_executable(ui_shot_satcap tests/ui_screenshot_sat_capture.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_satcap PRIVATE src build/ui_shot_satcap_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_satcap PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# AI multi-session + 已摘要 annotation + streaming transient offscreen shot.
add_executable(ui_shot_ai tests/ui_screenshot_ai.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_ai PRIVATE src build/ui_shot_ai_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_ai PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# FFT control strip + demod/bandwidth-preset row offscreen shot (not in ctest).
add_executable(ui_shot_fftband tests/ui_screenshot_fft_band.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_fftband PRIVATE src build/ui_shot_fftband_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_fftband PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# AI-panel autonomous-task step list offscreen shot (not in ctest).
add_executable(ui_shot_task_steps tests/ui_screenshot_task_steps.cpp ${SHOT_SRCS} ${HEADERS})
target_include_directories(ui_shot_task_steps PRIVATE src build/ui_shot_task_steps_autogen/include build/mbdsdr_autogen/include)
target_link_libraries(ui_shot_task_steps PRIVATE Qt6::Widgets Qt6::Multimedia Qt6::Network Qt6::Concurrent)

# M3 dual-protocol layer (OpenAI + Anthropic). Pure functions, no network.
add_executable(test_llm_protocol tests/test_llm_protocol.cpp src/ai/llm_protocol.cpp)
target_include_directories(test_llm_protocol PRIVATE src)
target_link_libraries(test_llm_protocol PRIVATE Qt6::Test Qt6::Core)
add_test(NAME llm_protocol COMMAND test_llm_protocol)

# M2 arguments validator: pure-function JSON-Schema arg validation (offline).
add_executable(test_arguments_validator tests/test_arguments_validator.cpp
    src/ai/arguments_validator.cpp)
target_include_directories(test_arguments_validator PRIVATE src)
target_link_libraries(test_arguments_validator PRIVATE Qt6::Test Qt6::Core)
add_test(NAME arguments_validator COMMAND test_arguments_validator)

# ---- M1: declarative tool JSON-Schema registry (pure, offline) ------------
# tool_schema.cpp includes core/tokens.h (which unconditionally pulls QtGui
# headers: QColor/QGuiApplication/QScreen) and ai/llm_client.h (QtNetwork header
# for QNetworkAccessManager), so the offscreen test target links Gui+Network
# purely for their interface include dirs. No network call is ever made.
add_executable(test_tool_schema tests/test_tool_schema.cpp src/ai/tool_schema.cpp)
target_include_directories(test_tool_schema PRIVATE src)
target_link_libraries(test_tool_schema PRIVATE Qt6::Test Qt6::Core Qt6::Gui Qt6::Network)
add_test(NAME tool_schema COMMAND test_tool_schema)

# ---- M4: real function-calling loop (transport-injected, fully offline) ------
# Drives LLMWorker with a canned mock transport (setDefaultTransportForTests),
# so no socket / no API key is ever needed. Mirrors the test_agent source set
# (llm_client/llm_worker/agent_tools/ai_config/ai_context) plus the M1-M3 units.
add_executable(test_ai_tool_loop tests/test_ai_tool_loop.cpp
    src/dsp/frequency_calibrator.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/file_source.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ai/llm_client.cpp src/ai/llm_worker.cpp src/ai/agent_tools.cpp
    src/ai/ai_config.cpp src/ai/ai_context.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ui/bookmark_manager.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp)
target_include_directories(test_ai_tool_loop PRIVATE src)
target_link_libraries(test_ai_tool_loop PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME ai_tool_loop COMMAND test_ai_tool_loop)

# ---- W2d A4 real-link closed loop (offline, deterministic) ------------------
# Source-disclosure honesty (test signal / not connected), scan_band synthetic
# annotation, start_recording bool propagation, get_status alignment, and the
# read-only predict_passes tool over the fresh-TLE-cache seam. No network / no
# API key: the engine defaults to the offline test source and pass prediction
# uses the pure predictPassesFromEntries seam with a fixed known TLE.
add_executable(test_ai_real_link tests/test_ai_real_link.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ai/llm_client.cpp
    src/ai/llm_worker.cpp src/ai/ai_config.cpp src/ai/agent_tools.cpp src/ai/ai_context.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp
    src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp
    src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp
    src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ui/bookmark_manager.cpp)
target_include_directories(test_ai_real_link PRIVATE src)
target_link_libraries(test_ai_real_link PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME ai_real_link COMMAND test_ai_real_link)

# ---- L5: C++ tool-registry completeness / consistency audit (offline) -------
# Introspects BOTH the declarative schema table (tool_schema.cpp) and the
# self-registered dispatch table (agent_tools.cpp) and asserts they agree, pins
# the 6-write/2-read split, and checks the unknown-tool honest path. Uses the
# offline TestSignalSource engine; no network / no API key / no hardware.
add_executable(test_tool_registry tests/test_tool_registry.cpp
    src/dsp/frequency_calibrator.cpp
    src/ai/sat_task_planner.cpp src/ai/task_orchestrator.cpp src/ai/llm_client.cpp
    src/ai/llm_worker.cpp src/ai/ai_config.cpp src/ai/agent_tools.cpp src/ai/ai_context.cpp
    src/ai/tool_schema.cpp src/ai/arguments_validator.cpp src/ai/llm_protocol.cpp
    src/dsp/tle_client.cpp src/dsp/sgp4.cpp src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp
    src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp src/dsp/rtl_tcp_source.cpp
    src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp
    src/dsp/channelizer.cpp src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp
    src/dsp/squelch.cpp src/dsp/agc.cpp src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp
    src/dsp/recorder.cpp src/dsp/wav_writer.cpp src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp
    src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp
    src/ui/bookmark_manager.cpp)
target_include_directories(test_tool_registry PRIVATE src)
target_link_libraries(test_tool_registry PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia Qt6::Concurrent)
add_test(NAME tool_registry COMMAND test_tool_registry)

# ---- P3 desktop polish (phase8): appended, existing registrations untouched ----
# Pure shortcut-catalog + tuning arithmetic (header-only, no QApplication).
add_executable(test_shortcut_catalog tests/test_shortcut_catalog.cpp)
target_include_directories(test_shortcut_catalog PRIVATE src)
target_link_libraries(test_shortcut_catalog PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME shortcut_catalog COMMAND test_shortcut_catalog)

# Pure status-strip formatting (header-only formatters).
add_executable(test_status_format tests/test_status_format.cpp)
target_include_directories(test_status_format PRIVATE src)
target_link_libraries(test_status_format PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME status_format COMMAND test_status_format)

# ---- P3 (phase9): Spacetime-view state->text/role formatters (header-only) ----
# Pure mapping, no QApplication: honest empty states + the system-never->GNSS rule.
add_executable(test_spacetime_format tests/test_spacetime_format.cpp)
target_include_directories(test_spacetime_format PRIVATE src)
target_link_libraries(test_spacetime_format PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME spacetime_format COMMAND test_spacetime_format)


# ---- P3 (phase11): spectrum interaction pure arithmetic (header-only) -------
# Click-vs-drag settle, wheel step-snap, edge-drag bandwidth clamp, divider
# share clamp. No QApplication: the rules are now header-only and unit-tested
# directly; spectrum_display.cpp delegates to them.
add_executable(test_spectrum_tune tests/test_spectrum_tune.cpp)
target_include_directories(test_spectrum_tune PRIVATE src)
target_link_libraries(test_spectrum_tune PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME spectrum_tune COMMAND test_spectrum_tune)

# ---- Phase12 waterfall render (clean-room SDR++ mechanism, header-only) ------
# doZoom block-max decimation, 256-entry LUT build, dB->LUT range re-map,
# external colormap JSON parse (valid/invalid/fallback), and nice frequency
# ticks that follow zoom/pan. No QApplication: pure arithmetic, unit-tested
# directly; spectrum_display.cpp delegates to them.
add_executable(test_spectrum_render tests/test_spectrum_render.cpp)
target_include_directories(test_spectrum_render PRIVATE src)
target_link_libraries(test_spectrum_render PRIVATE Qt6::Test Qt6::Core Qt6::Gui)
add_test(NAME spectrum_render COMMAND test_spectrum_render)

# ---- phase12 Wave2: RTL-SDR device-layer robustness (gap-analysis L3 + L4) ---
# L3: tune retry + get_center_freq readback (clean-room re-derivation of the
#     SDR++ RTL2832 PLL write-loss defence, main.cpp:344-359).
# L4: start() explicitly asks tuner bandwidth = 0 (driver auto, main.cpp:310).
# Driven by an injected FAKE librtlsdr ops table -- a test double, NOT a
# hardware mock: no USB / no RTL2832, deterministic recorded call log.
add_executable(test_rtl_sdr_tune tests/test_rtl_sdr_tune.cpp
    src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp
    src/dsp/device_capabilities.cpp)
target_include_directories(test_rtl_sdr_tune PRIVATE src)
target_link_libraries(test_rtl_sdr_tune PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rtl_sdr_tune COMMAND test_rtl_sdr_tune)

# ---- phase12 Wave2: channelizer rational resampling (gap-analysis L1) -------
# Clean-room two-rate chain: power-of-two pre-decimation (the decimating FIR) +
# GCD-reduced polyphase rational residual (rational_resampler.h). Fixes the
# 2.048MHz->48kHz round(in/out)=43 drift (was 47.62kHz). Integer-exact ratios
# still take the single-stage integer path. Header-only resampler, so no new
# translation unit is added to the other per-target source lists.
add_executable(test_rational_resampler tests/test_rational_resampler.cpp
    src/dsp/channelizer.cpp src/dsp/fft.cpp)
target_include_directories(test_rational_resampler PRIVATE src)
target_link_libraries(test_rational_resampler PRIVATE Qt6::Test Qt6::Core)
add_test(NAME rational_resampler COMMAND test_rational_resampler)

# ---- phase12 Wave1-C: demodulator gap-closing (L9/L10/L13) ------------------
# L9  in-chain AM carrier AGC (ComplexCarrierAgc, on complex IQ before |·|).
# L10 NFM bandwidth-matched Nuttall FIR (cutoff=bw/2, trans=0.1*bw, hot
#     re-design on setBandwidth).
# L13 real 2-FSK receiver: quadrature discriminator -> baseband Nuttall LPF ->
#     Mueller-Muller strobe + sign decision. All deterministic synthetic signals.
add_executable(test_demod_enhance tests/test_demod_enhance.cpp
    src/dsp/demod.cpp src/dsp/agc.cpp src/dsp/fsk_demod.cpp)
target_include_directories(test_demod_enhance PRIVATE src)
target_link_libraries(test_demod_enhance PRIVATE Qt6::Test Qt6::Core)
add_test(NAME demod_enhance COMMAND test_demod_enhance)

# ---- phase13: RxVFO bandwidth short-circuit + transition-band parameterization
# Clean-room mapping of SDR++ rx_vfo.h (GPLv3, mechanism only):
#   filterNeeded = (bandwidth != outRate); transition width = 0.1*(bw/2).
# Pure decision (planChannelFilter), Channelizer whole-band passthrough fast
# path, and NCO retune phase continuity. Links channelizer only (no FFT).
add_executable(test_bandwidth_shortcut tests/test_bandwidth_shortcut.cpp
    src/dsp/channelizer.cpp)
target_include_directories(test_bandwidth_shortcut PRIVATE src)
target_link_libraries(test_bandwidth_shortcut PRIVATE Qt6::Test Qt6::Core)
add_test(NAME bandwidth_shortcut COMMAND test_bandwidth_shortcut)

# ---- phase24 block1: demodulated-audio network sink (SDR++ network_sink) ------
# Raw 16-bit LE PCM stream of the 48 kHz demodulated audio over UDP (send to
# host:port) or TCP (single-client listening server), benchmarked on the SDR++
# module (GPLv3, mechanism only). The engine installs it as a PARALLEL tap off
# the existing audio write point -- playback is never diverted. Loopback tests
# prove byte-exact wiring, honest idle/no-client/disconnect states, and the
# engine tap e2e (synthetic offline IQ) matches the MemoryAudioSink capture.
add_executable(test_network_audio_sink tests/test_network_audio_sink.cpp
    src/dsp/network_audio_sink.cpp
    src/dsp/spectrum_engine.cpp src/dsp/device_capabilities.cpp src/dsp/test_signal.cpp src/dsp/rtl_sdr_source.cpp src/dsp/tuner_gain_table.cpp src/dsp/stream_watchdog.cpp src/dsp/file_source.cpp
    src/dsp/rtl_tcp_source.cpp src/dsp/iq_frontend.cpp src/dsp/vfo_manager.cpp src/dsp/fsk_demod.cpp src/dsp/pocsag_decoder.cpp src/dsp/m17_decoder.cpp src/dsp/vor_receiver.cpp src/dsp/digital_demod.cpp src/dsp/anr.cpp src/dsp/channelizer.cpp
    src/dsp/audio_resampler.cpp src/dsp/demod.cpp src/dsp/wfm_stereo.cpp src/dsp/squelch.cpp src/dsp/agc.cpp
    src/dsp/audio_output.cpp src/dsp/qt_audio_sink.cpp src/dsp/memory_audio_sink.cpp src/dsp/recorder.cpp src/dsp/wav_writer.cpp
    src/dsp/gated_recorder.cpp src/dsp/signal_watch.cpp src/dsp/cw_decoder.cpp src/dsp/adsb_decoder.cpp src/dsp/rds_decoder.cpp src/dsp/apt_decoder.cpp
    src/dsp/fft.cpp src/dsp/power_spectrum.cpp src/dsp/noise_blanker.cpp src/dsp/peak_detector.cpp)
target_include_directories(test_network_audio_sink PRIVATE src)
target_link_libraries(test_network_audio_sink PRIVATE Qt6::Test Qt6::Core Qt6::Network Qt6::Widgets Qt6::Multimedia)
add_test(NAME network_audio_sink COMMAND test_network_audio_sink)
