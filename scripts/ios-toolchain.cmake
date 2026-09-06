set(CMAKE_SYSTEM_NAME iOS)
set(CMAKE_OSX_SYSROOT "$ENV{SDKROOT}" CACHE STRING "Apple SDK")
set(CMAKE_OSX_ARCHITECTURES arm64 CACHE STRING "Apple architecture")
set(CMAKE_OSX_DEPLOYMENT_TARGET 26.0 CACHE STRING "Minimum iOS")
set(SPM_ENABLE_SHARED OFF CACHE BOOL "Build static SentencePiece")

# SentencePiece calls this helper for its CLI targets when targeting iOS, but
# only defines it in its standalone Xcode toolchain. Supply it for Cargo/CMake.
function(set_xcode_property target property value variant)
    if(TARGET ${target})
        set_property(TARGET ${target} PROPERTY XCODE_ATTRIBUTE_${property} "${value}")
    endif()
endfunction()
