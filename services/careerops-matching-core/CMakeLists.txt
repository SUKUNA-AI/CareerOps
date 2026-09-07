cmake_minimum_required(VERSION 3.25)
project(careerops_matching_core VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Protobuf REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_program(PROTOC_EXECUTABLE protoc REQUIRED)
find_program(GRPC_CPP_PLUGIN grpc_cpp_plugin REQUIRED)

set(PROTO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../proto")
set(PROTO_RELATIVE "careerops/matching_core/v1/control.proto")
set(PROTO_FILE "${PROTO_ROOT}/${PROTO_RELATIVE}")
set(GENERATED_DIR "${CMAKE_CURRENT_BINARY_DIR}/generated")
set(GENERATED_PREFIX "${GENERATED_DIR}/careerops/matching_core/v1/control")

add_custom_command(
    OUTPUT
        "${GENERATED_PREFIX}.pb.cc"
        "${GENERATED_PREFIX}.pb.h"
        "${GENERATED_PREFIX}.grpc.pb.cc"
        "${GENERATED_PREFIX}.grpc.pb.h"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${GENERATED_DIR}"
    COMMAND
        "${PROTOC_EXECUTABLE}"
        "--proto_path=${PROTO_ROOT}"
        "--cpp_out=${GENERATED_DIR}"
        "--grpc_out=${GENERATED_DIR}"
        "--plugin=protoc-gen-grpc=${GRPC_CPP_PLUGIN}"
        "${PROTO_FILE}"
    DEPENDS "${PROTO_FILE}"
    VERBATIM
)

add_library(matching_core_proto STATIC
    "${GENERATED_PREFIX}.pb.cc"
    "${GENERATED_PREFIX}.grpc.pb.cc"
)

target_include_directories(matching_core_proto PUBLIC "${GENERATED_DIR}")
target_link_libraries(matching_core_proto PUBLIC gRPC::grpc++ protobuf::libprotobuf)

add_executable(careerops-matching-core src/main.cpp)
target_link_libraries(careerops-matching-core PRIVATE matching_core_proto)
target_compile_options(careerops-matching-core PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_compile_definitions(
    careerops-matching-core
    PRIVATE CAREEROPS_MATCHING_CORE_VERSION="${PROJECT_VERSION}"
)

install(TARGETS careerops-matching-core RUNTIME DESTINATION bin)
