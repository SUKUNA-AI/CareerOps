#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>

#include <grpcpp/grpcpp.h>

#include "careerops/matching_core/v1/control.grpc.pb.h"

#ifndef CAREEROPS_MATCHING_CORE_VERSION
#define CAREEROPS_MATCHING_CORE_VERSION "0.0.0"
#endif

namespace matching = careerops::matching_core::v1;

namespace {

constexpr char kServiceName[] = "careerops-matching-core";
constexpr char kControlProtocolVersion[] = "careerops.matching-core.control.v1";
constexpr char kDefaultListenAddress[] = "127.0.0.1:50051";

class MatchingCoreControlService final : public matching::MatchingCoreControl::Service {
 public:
  grpc::Status Health(
      grpc::ServerContext* context,
      const matching::HealthRequest* request,
      matching::HealthResponse* response) override {
    (void)context;
    (void)request;
    response->set_status(matching::HealthResponse::SERVING);
    response->set_service_name(kServiceName);
    response->set_service_version(CAREEROPS_MATCHING_CORE_VERSION);
    response->set_protocol_version(kControlProtocolVersion);
    return grpc::Status::OK;
  }

  grpc::Status GetCapabilities(
      grpc::ServerContext* context,
      const matching::CapabilitiesRequest* request,
      matching::CapabilitiesResponse* response) override {
    (void)context;
    (void)request;
    response->set_service_name(kServiceName);
    response->set_service_version(CAREEROPS_MATCHING_CORE_VERSION);
    response->add_supported_control_protocol_versions(kControlProtocolVersion);
    response->set_evaluate_match_available(false);
    response->set_evaluate_batch_available(false);
    return grpc::Status::OK;
  }
};

std::string ListenAddress() {
  const char* configured = std::getenv("CAREEROPS_MATCHING_CORE_LISTEN_ADDR");
  if (configured == nullptr || std::string(configured).empty()) {
    return kDefaultListenAddress;
  }
  return configured;
}

}  // namespace

int main() {
  const std::string listen_address = ListenAddress();
  MatchingCoreControlService service;

  grpc::ServerBuilder builder;
  builder.AddListeningPort(listen_address, grpc::InsecureServerCredentials());
  builder.RegisterService(&service);

  std::unique_ptr<grpc::Server> server = builder.BuildAndStart();
  if (server == nullptr) {
    std::cerr << "failed to start " << kServiceName << " on " << listen_address << '\n';
    return EXIT_FAILURE;
  }

  std::cout << kServiceName << " listening on " << listen_address << '\n';
  server->Wait();
  return EXIT_SUCCESS;
}
