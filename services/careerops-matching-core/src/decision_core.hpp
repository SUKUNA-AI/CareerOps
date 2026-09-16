#pragma once

#include <functional>

#include <google/protobuf/struct.pb.h>

namespace careerops::matching_core {

inline constexpr char kDecisionProtocolVersion[] = "careerops.matching-core.decision.v1";

google::protobuf::Struct EvaluateDecision(const google::protobuf::Struct& request);
google::protobuf::Struct EvaluateBatch(const google::protobuf::Struct& request);

}  // namespace careerops::matching_core
