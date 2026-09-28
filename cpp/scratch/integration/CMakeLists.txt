# SPDX-License-Identifier: MIT
# Throwaway offscreen screenshot build for the integrated map + sky/elevation.
# Standalone: does NOT touch the project CMakeLists.txt.
#
#   cmake -S . -B build -DCMAKE_PREFIX_PATH=/home/user/Qt/6.8.2/gcc_64
#   cmake --build build -j
cmake_minimum_required(VERSION 3.16)
project(mbdsdr_integration_shot CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC OFF)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets)

add_executable(shot_new
    ${CMAKE_CURRENT_SOURCE_DIR}/shot_new.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../src/ui/world_view.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../src/ui/sky_view.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../src/ui/elevation_plot.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../src/ui/map_projection.cpp
)
target_include_directories(shot_new PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../src
)
target_link_libraries(shot_new PRIVATE Qt6::Widgets Qt6::Gui Qt6::Core)
