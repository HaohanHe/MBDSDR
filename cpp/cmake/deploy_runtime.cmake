# SPDX-License-Identifier: MIT
# deploy_runtime.cmake — 构建期运行库部署脚本（cmake -P 调用）。
#
# 设计要点（2026-11-03 直跑弹窗三连后钉死）：
#  1) 自动：挂在每个 ALL 可执行目标的 POST_BUILD 上——任何一次构建后 exe 旁
#     即完整，不依赖开发者记得跑 deploy_win_runtime 目标（旧缺陷：独立目标
#     需显式执行，构建测试不触发，cpp\build 树因此 0 DLL 半部署）。
#  2) 幂等增量：逐文件 copy_if_different——已存在且内容一致即跳过（源时间
#     不变时近零成本），绝不每次全量拷贝拖慢构建。
#  3) 宁红不静：源文件必须真实存在（Qt6Core.dll / MinGW 三件套 / platforms
#     插件逐一 Test-Path），缺了直接 FATAL_ERROR 构建期报错——不许静默漏拷
#     留「能 build 但跑不了」的树。
#
# 输入（-D 传入）：RUNTIME_OUTPUT_DIRECTORY / QT_BIN / TC_BIN / QT_PLUGINS /
# RTLSDR_DIRS（分号分隔，可为空）。

cmake_minimum_required(VERSION 3.16)

set(_out "${RUNTIME_OUTPUT_DIRECTORY}")
if(NOT IS_DIRECTORY "${_out}")
  message(FATAL_ERROR "部署目标目录不存在: ${_out}")
endif()

set(_dlls "")
# Qt6*.dll 全量（含 Qt6Test——windeployqt 不显式收它，测试 exe 链接 Qt6::Test）
file(GLOB _qt6 "${QT_BIN}/Qt6*.dll")
list(APPEND _dlls ${_qt6})
# Qt 多媒体/OpenGL 辅助运行库（avcodec/avformat/avutil/swresample/swscale/
# D3Dcompiler_47/opengl32sw 等，Qt6Multimedia/Widgets 可选依赖，windeployqt 同口径）
file(GLOB _aux "${QT_BIN}/*.dll")
foreach(_f IN LISTS _aux)
  get_filename_component(_n "${_f}" NAME)
  if(NOT _n MATCHES "^Qt6")
    list(APPEND _dlls "${_f}")
  endif()
endforeach()
# MinGW 运行三件套（显式存在性校验，缺即红）
foreach(_mingw libgcc_s_seh-1.dll libstdc++-6.dll libwinpthread-1.dll)
  if(NOT EXISTS "${TC_BIN}/${_mingw}")
    message(FATAL_ERROR "MinGW 运行库缺失: ${TC_BIN}/${_mingw}")
  endif()
  list(APPEND _dlls "${TC_BIN}/${_mingw}")
endforeach()
# RTL-SDR 真库启用时
foreach(_rtl_dir IN LISTS RTLSDR_DIRS)
  file(GLOB _rtl_dlls "${_rtl_dir}/*.dll")
  list(APPEND _dlls ${_rtl_dlls})
endforeach()

if(NOT EXISTS "${QT_BIN}/Qt6Core.dll")
  message(FATAL_ERROR "Qt bin 缺失 Qt6Core.dll: ${QT_BIN}")
endif()

foreach(_f IN LISTS _dlls)
  if(NOT EXISTS "${_f}")
    message(FATAL_ERROR "部署源缺失: ${_f}")
  endif()
  execute_process(COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${_f}" "${_out}/" RESULT_VARIABLE _rc)
  if(_rc)
    message(FATAL_ERROR "拷贝失败: ${_f} -> ${_out} (rc=${_rc})")
  endif()
endforeach()

# Qt 平台插件：qwindows（GUI）+ qoffscreen（无头）落 exe 旁 platforms/
if(NOT EXISTS "${QT_PLUGINS}/platforms/qwindows.dll")
  message(FATAL_ERROR "Qt 平台插件缺失: ${QT_PLUGINS}/platforms/qwindows.dll")
endif()
if(NOT EXISTS "${QT_PLUGINS}/platforms/qoffscreen.dll")
  message(FATAL_ERROR "Qt 平台插件缺失: ${QT_PLUGINS}/platforms/qoffscreen.dll")
endif()
file(MAKE_DIRECTORY "${_out}/platforms")
foreach(_p qwindows.dll qoffscreen.dll)
  execute_process(COMMAND ${CMAKE_COMMAND} -E copy_if_different
      "${QT_PLUGINS}/platforms/${_p}" "${_out}/platforms/"
      RESULT_VARIABLE _rc)
  if(_rc)
    message(FATAL_ERROR "拷贝插件失败: ${_p} (rc=${_rc})")
  endif()
endforeach()
