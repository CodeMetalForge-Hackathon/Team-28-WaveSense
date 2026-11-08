#!/bin/sh
set -eu
APP_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SDK_DIR=${LIVOX_SDK_DIR:-$APP_DIR/.deps/Livox-SDK2}
SDK_COMMIT=c0796f04c143143899c87a773d9f6b7136453c0b
if [ ! -d "$SDK_DIR" ]; then
 mkdir -p "$(dirname -- "$SDK_DIR")"
 git clone https://github.com/Livox-SDK/Livox-SDK2.git "$SDK_DIR"
 git -C "$SDK_DIR" checkout "$SDK_COMMIT"
fi
cmake -S "$SDK_DIR" -B "$SDK_DIR/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build "$SDK_DIR/build" --target livox_lidar_sdk_static -j2
mkdir -p "$APP_DIR/bin"
g++ -std=c++11 -O2 -pthread -Wall -Wextra -I"$SDK_DIR/include" "$APP_DIR/livox/bridge.cpp" "$SDK_DIR/build/sdk_core/liblivox_lidar_sdk_static.a" -o "$APP_DIR/bin/livox-bridge"
