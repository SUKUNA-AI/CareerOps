#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>

#include <grpcpp/grpcpp.h>

#include "careerops/matching_core/v1/control.grpc.pb.h"
#include "decision_core.hpp"

#ifndef CAREEROPS_MATCHING_CORE_VERSION
#define CAREEROPS_MATCHING_CORE_VERSION "0.0.0"
#endif

namespace matching = careerops::matching_core::v1;
namespace core = careerops::matching_core;

namespace {

constexpr char kServiceName[] = "careerops-matching-core";
constexpr char kControlProtocolVersion[] = "careerops.matching-core.control.v1";
constexpr char kDefaultListenAddress[] = "127.0.0.1:50051";

bool IsDigit(char value) { return value >= '0' && value <= '9'; }

int ParseDigits(std::string_view value, std::size_t offset, std::size_t count) {
  int result = 0;
  for (std::size_t index = offset; index < offset + count; ++index) {
    if (!IsDigit(value[index])) throw std::invalid_argument("invalid date");
    result = result * 10 + (value[index] - '0');
  }
  return result;
}

bool IsLeapYear(int year) {
  return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

void ValidateIsoDate(std::string_view value) {
  if (value.size() != 10 || value[4] != '-' || value[7] != '-') {
    throw std::invalid_argument("invalid date");
  }
  const int year = ParseDigits(value, 0, 4);
  const int month = ParseDigits(value, 5, 2);
  const int day = ParseDigits(value, 8, 2);
  if (year < 1 || month < 1 || month > 12 || day < 1) {
    throw std::invalid_argument("invalid date");
  }
  constexpr int kDaysByMonth[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  int max_day = kDaysByMonth[month - 1];
  if (month == 2 && IsLeapYear(year)) max_day = 29;
  if (day > max_day) throw std::invalid_argument("invalid date");
}

bool IsDateField(std::string_view field_name) {
  return field_name == "as_of" || field_name == "start_date" || field_name == "end_date";
}

void ValidateDecisionValue(const google::protobuf::Value& value, std::string_view field_name);

void ValidateDecisionStruct(const google::protobuf::Struct& object) {
  for (const auto& [name, value] : object.fields()) ValidateDecisionValue(value, name);
}

void ValidateDecisionValue(const google::protobuf::Value& value, std::string_view field_name) {
  switch (value.kind_case()) {
    case google::protobuf::Value::kStringValue:
      if (IsDateField(field_name) && !value.string_value().empty()) {
        ValidateIsoDate(value.string_value());
      }
      return;
    case google::protobuf::Value::kStructValue:
      ValidateDecisionStruct(value.struct_value());
      return;
    case google::protobuf::Value::kListValue:
      for (const auto& item : value.list_value().values()) ValidateDecisionValue(item, field_name);
      return;
    default:
      return;
  }
}

class MatchingCoreControlService final : public matching::MatchingCoreControl::Service {
 public:
  grpc::Status Health(grpc::ServerContext* context, const matching::HealthRequest* request,
                      matching::HealthResponse* response) override {
    (void)context;
    (void)request;
    response->set_status(matching::HealthResponse::SERVING);
    response->set_service_name(kServiceName);
    response->set_service_version(CAREEROPS_MATCHING_CORE_VERSION);
    response->set_protocol_version(kControlProtocolVersion);
    return grpc::Status::OK;
  }

  grpc::Status GetCapabilities(grpc::ServerContext* context,
                               const matching::CapabilitiesRequest* request,
                               matching::CapabilitiesResponse* response) override {
    (void)context;
    (void)request;
    response->set_service_name(kServiceName);
    response->set_service_version(CAREEROPS_MATCHING_CORE_VERSION);
    response->add_supported_control_protocol_versions(kControlProtocolVersion);
    response->set_evaluate_match_available(true);
    response->set_evaluate_batch_available(true);
    return grpc::Status::OK;
  }
};

class MatchingCoreDecisionService final : public matching::MatchingCoreDecision::Service {
 public:
  grpc::Status Evaluate(grpc::ServerContext* context, const google::protobuf::Struct* request,
                        google::protobuf::Struct* response) override {
    (void)context;
    try {
      ValidateDecisionStruct(*request);
      *response = core::EvaluateDecision(*request);
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, error.what());
    }
  }

  grpc::Status EvaluateBatch(grpc::ServerContext* context,
                             const google::protobuf::Struct* request,
                             google::protobuf::Struct* response) override {
    (void)context;
    try {
      ValidateDecisionStruct(*request);
      *response = core::EvaluateBatch(*request);
      return grpc::Status::OK;
    } catch (const std::exception& error) {
      return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, error.what());
    }
  }
};

std::string ListenAddress() {
  const char* configured = std::getenv("CAREEROPS_MATCHING_CORE_LISTEN_ADDR");
  if (configured == nullptr || std::string(configured).empty()) return kDefaultListenAddress;
  return configured;
}

int RunHealthcheck(const std::string& target) {
  auto channel = grpc::CreateChannel(target, grpc::InsecureChannelCredentials());
  auto stub = matching::MatchingCoreControl::NewStub(channel);
  grpc::ClientContext context;
  context.set_deadline(std::chrono::system_clock::now() + std::chrono::seconds(2));
  matching::HealthRequest request;
  matching::HealthResponse response;
  const grpc::Status status = stub->Health(&context, request, &response);
  return (!status.ok() || response.status() != matching::HealthResponse::SERVING)
             ? EXIT_FAILURE
             : EXIT_SUCCESS;
}

int RunServer() {
  const std::string listen_address = ListenAddress();
  MatchingCoreControlService control_service;
  MatchingCoreDecisionService decision_service;
  grpc::ServerBuilder builder;
  builder.AddListeningPort(listen_address, grpc::InsecureServerCredentials());
  builder.RegisterService(&control_service);
  builder.RegisterService(&decision_service);
  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (server == nullptr) {
    std::cerr << "failed to start " << kServiceName << " on " << listen_address << '\n';
    return EXIT_FAILURE;
  }
  std::cout << kServiceName << " listening on " << listen_address << '\n';
  server->Wait();
  return EXIT_SUCCESS;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc == 1) return RunServer();
  if (argc == 2 && std::string(argv[1]) == "--healthcheck") return RunHealthcheck(ListenAddress());
  std::cerr << "usage: careerops-matching-core [--healthcheck]\n";
  return EXIT_FAILURE;
}
