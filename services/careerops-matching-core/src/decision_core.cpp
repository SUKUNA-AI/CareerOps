#include "decision_core.hpp"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <map>
#include <optional>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#include <boost/multiprecision/cpp_dec_float.hpp>
#include <google/protobuf/struct.pb.h>

namespace careerops::matching_core {
namespace {

using Decimal = boost::multiprecision::number<boost::multiprecision::cpp_dec_float<50>>;
using google::protobuf::ListValue;
using google::protobuf::Struct;
using google::protobuf::Value;

const Decimal kZero("0");
const Decimal kOne("1");
const Decimal kHundred("100");
const Decimal kDaysPerYear("365.2425");

[[noreturn]] void Fail(const std::string& message) { throw std::runtime_error(message); }

const Value* Find(const Struct& object, std::string_view key) {
  const auto it = object.fields().find(std::string(key));
  return it == object.fields().end() ? nullptr : &it->second;
}

const Value& Require(const Struct& object, std::string_view key) {
  const Value* value = Find(object, key);
  if (value == nullptr) Fail("missing field: " + std::string(key));
  return *value;
}

const Struct& AsStruct(const Value& value, std::string_view name) {
  if (value.kind_case() != Value::kStructValue) Fail(std::string(name) + " must be object");
  return value.struct_value();
}

const ListValue& AsList(const Value& value, std::string_view name) {
  if (value.kind_case() != Value::kListValue) Fail(std::string(name) + " must be list");
  return value.list_value();
}

std::string AsString(const Value& value, std::string_view name) {
  if (value.kind_case() != Value::kStringValue) Fail(std::string(name) + " must be string");
  return value.string_value();
}

bool AsBool(const Value& value, std::string_view name) {
  if (value.kind_case() != Value::kBoolValue) Fail(std::string(name) + " must be bool");
  return value.bool_value();
}

std::optional<std::string> OptionalString(const Struct& object, std::string_view key) {
  const Value* value = Find(object, key);
  if (value == nullptr || value->kind_case() == Value::kNullValue) return std::nullopt;
  return AsString(*value, key);
}

std::string StringField(const Struct& object, std::string_view key) {
  return AsString(Require(object, key), key);
}

bool BoolField(const Struct& object, std::string_view key) {
  return AsBool(Require(object, key), key);
}

Decimal ParseDecimal(const std::string& text, std::string_view name) {
  try {
    return Decimal(text);
  } catch (const std::exception&) {
    Fail(std::string(name) + " must be decimal string");
  }
}

std::optional<Decimal> OptionalDecimal(const Struct& object, std::string_view key) {
  const auto text = OptionalString(object, key);
  return text.has_value() ? std::optional<Decimal>(ParseDecimal(*text, key)) : std::nullopt;
}

std::string DecimalString(const Decimal& value) {
  if (value == 0) return "0";
  std::string result = value.str(28, std::ios_base::fmtflags(0));
  const auto exponent = result.find_first_of("eE");
  std::string mantissa = exponent == std::string::npos ? result : result.substr(0, exponent);
  const std::string suffix = exponent == std::string::npos ? "" : result.substr(exponent);
  if (mantissa.find('.') != std::string::npos) {
    while (!mantissa.empty() && mantissa.back() == '0') mantissa.pop_back();
    if (!mantissa.empty() && mantissa.back() == '.') mantissa.pop_back();
  }
  return mantissa + suffix;
}

void PutString(Struct* object, const std::string& key, const std::string& value) {
  (*object->mutable_fields())[key].set_string_value(value);
}

void PutBool(Struct* object, const std::string& key, bool value) {
  (*object->mutable_fields())[key].set_bool_value(value);
}

void PutNull(Struct* object, const std::string& key) {
  (*object->mutable_fields())[key].set_null_value(google::protobuf::NULL_VALUE);
}

void PutStruct(Struct* object, const std::string& key, const Struct& value) {
  *(*object->mutable_fields())[key].mutable_struct_value() = value;
}

void PutStrings(Struct* object, const std::string& key, const std::vector<std::string>& values) {
  auto* list = (*object->mutable_fields())[key].mutable_list_value();
  for (const auto& value : values) list->add_values()->set_string_value(value);
}

void PutStructs(Struct* object, const std::string& key, const std::vector<Struct>& values) {
  auto* list = (*object->mutable_fields())[key].mutable_list_value();
  for (const auto& value : values) *list->add_values()->mutable_struct_value() = value;
}

std::vector<std::string> StringList(const Struct& object, std::string_view key) {
  const Value* value = Find(object, key);
  if (value == nullptr) return {};
  std::vector<std::string> result;
  for (const auto& item : AsList(*value, key).values()) result.push_back(AsString(item, key));
  return result;
}

std::string Normalize(std::string_view input) {
  std::string out;
  out.reserve(input.size());
  bool previous_space = true;
  for (std::size_t i = 0; i < input.size();) {
    const unsigned char c = static_cast<unsigned char>(input[i]);
    if (c < 0x80) {
      char value = static_cast<char>(std::tolower(c));
      if (std::isspace(c)) {
        if (!previous_space) out.push_back(' ');
        previous_space = true;
      } else {
        out.push_back(value);
        previous_space = false;
      }
      ++i;
      continue;
    }
    if (i + 1 < input.size()) {
      const unsigned char c2 = static_cast<unsigned char>(input[i + 1]);
      // Cyrillic upper А-П -> а-п.
      if (c == 0xD0 && c2 >= 0x90 && c2 <= 0x9F) {
        out.push_back(static_cast<char>(0xD0));
        out.push_back(static_cast<char>(c2 + 0x20));
        previous_space = false;
        i += 2;
        continue;
      }
      // Cyrillic upper Р-Я -> р-я.
      if (c == 0xD0 && c2 >= 0xA0 && c2 <= 0xAF) {
        out.push_back(static_cast<char>(0xD1));
        out.push_back(static_cast<char>(c2 - 0x20));
        previous_space = false;
        i += 2;
        continue;
      }
      // Ё/ё -> е to mirror Python normalizer.
      if ((c == 0xD0 && c2 == 0x81) || (c == 0xD1 && c2 == 0x91)) {
        out.push_back(static_cast<char>(0xD0));
        out.push_back(static_cast<char>(0xB5));
        previous_space = false;
        i += 2;
        continue;
      }
    }
    out.push_back(static_cast<char>(c));
    previous_space = false;
    ++i;
  }
  if (!out.empty() && out.back() == ' ') out.pop_back();
  return out;
}

bool Contains(const std::string& text, std::string_view needle) {
  return text.find(std::string(needle)) != std::string::npos;
}

struct Subject {
  std::string normalized;
  std::optional<std::string> dictionary_key;
};

struct Threshold {
  std::optional<Decimal> minimum;
  std::optional<Decimal> maximum;
};

struct Requirement {
  std::string id;
  std::string kind;
  std::vector<Subject> subjects;
  std::optional<std::string> activity;
  std::string importance;
  std::string modality;
  std::string polarity;
  std::optional<Threshold> threshold;
};

struct Group {
  std::string id;
  std::string op;
  std::vector<std::string> requirement_ids;
  std::vector<std::string> child_group_ids;
};

struct TimeSpan {
  std::optional<std::string> start;
  std::optional<std::string> end;
  std::optional<bool> current;
};

struct Evidence {
  std::string id;
  std::vector<Subject> subjects;
  std::optional<std::string> activity;
  std::string actor;
  std::string context;
  std::string polarity;
  std::string strength;
  std::optional<TimeSpan> time_span;
};

struct Selection {
  std::string requirement_id;
  std::string state;
  std::vector<std::string> pool_ids;
  std::vector<std::string> candidate_ids;
};

struct Qualification {
  std::string requirement_id;
  std::string state;
  Decimal lower{kZero};
  Decimal upper{kOne};
  bool selection_complete{false};
  std::vector<std::string> ranked_ids;
  std::vector<std::string> supporting_ids;
  std::vector<std::string> contradicting_ids;
  std::vector<std::string> ambiguous_ids;
  std::vector<std::string> reasons;
};

struct GroupQualification {
  std::string group_id;
  Decimal lower{kZero};
  Decimal upper{kOne};
  std::vector<std::string> reasons;
};

struct QualificationResult {
  std::vector<Qualification> evaluations;
  std::vector<GroupQualification> groups;
  std::vector<std::string> ignored;
};

std::vector<Subject> ParseSubjects(const Struct& object) {
  std::vector<Subject> result;
  const Value* value = Find(object, "subjects");
  if (value == nullptr) return result;
  for (const auto& item : AsList(*value, "subjects").values()) {
    const Struct& subject = AsStruct(item, "subject");
    result.push_back({StringField(subject, "normalized"), OptionalString(subject, "dictionary_key")});
  }
  return result;
}

std::vector<Requirement> ParseRequirements(const Struct& requirement_set) {
  std::vector<Requirement> result;
  for (const auto& item : AsList(Require(requirement_set, "requirements"), "requirements").values()) {
    const Struct& value = AsStruct(item, "requirement");
    Requirement requirement;
    requirement.id = StringField(value, "requirement_id");
    requirement.kind = StringField(value, "kind");
    requirement.subjects = ParseSubjects(value);
    requirement.activity = OptionalString(value, "activity");
    requirement.importance = StringField(value, "importance");
    requirement.modality = StringField(value, "modality");
    requirement.polarity = StringField(value, "polarity");
    const Value* threshold = Find(value, "threshold");
    if (threshold != nullptr && threshold->kind_case() != Value::kNullValue) {
      const Struct& source = AsStruct(*threshold, "threshold");
      if (StringField(source, "metric") != "experience_years") Fail("unsupported threshold metric");
      requirement.threshold = Threshold{OptionalDecimal(source, "minimum"), OptionalDecimal(source, "maximum")};
    }
    result.push_back(std::move(requirement));
  }
  return result;
}

std::vector<Group> ParseGroups(const Struct& requirement_set) {
  std::vector<Group> result;
  for (const auto& item : AsList(Require(requirement_set, "groups"), "groups").values()) {
    const Struct& value = AsStruct(item, "group");
    result.push_back({StringField(value, "group_id"), StringField(value, "operator"),
                      StringList(value, "requirement_ids"), StringList(value, "child_group_ids")});
  }
  return result;
}

std::vector<Evidence> ParseEvidence(const Struct& evidence_set) {
  std::vector<Evidence> result;
  for (const auto& item : AsList(Require(evidence_set, "evidence"), "evidence").values()) {
    const Struct& value = AsStruct(item, "evidence");
    Evidence evidence;
    evidence.id = StringField(value, "evidence_id");
    evidence.subjects = ParseSubjects(value);
    evidence.activity = OptionalString(value, "activity");
    evidence.actor = StringField(value, "actor_scope");
    evidence.context = StringField(value, "context");
    evidence.polarity = StringField(value, "polarity");
    evidence.strength = StringField(value, "strength");
    const Value* span = Find(value, "time_span");
    if (span != nullptr && span->kind_case() != Value::kNullValue) {
      const Struct& source = AsStruct(*span, "time_span");
      TimeSpan parsed{OptionalString(source, "start_date"), OptionalString(source, "end_date"), std::nullopt};
      const Value* current = Find(source, "currently_active");
      if (current != nullptr && current->kind_case() != Value::kNullValue) parsed.current = AsBool(*current, "currently_active");
      evidence.time_span = std::move(parsed);
    }
    result.push_back(std::move(evidence));
  }
  return result;
}

std::vector<Selection> ParseSelections(const Struct& candidate_set) {
  std::vector<Selection> result;
  for (const auto& item : AsList(Require(candidate_set, "selections"), "selections").values()) {
    const Struct& value = AsStruct(item, "selection");
    Selection selection;
    selection.requirement_id = StringField(value, "requirement_id");
    selection.state = StringField(value, "state");
    selection.pool_ids = StringList(value, "pool_evidence_ids");
    const Value* candidates = Find(value, "candidates");
    if (candidates != nullptr) {
      for (const auto& candidate : AsList(*candidates, "candidates").values()) {
        selection.candidate_ids.push_back(StringField(AsStruct(candidate, "candidate"), "evidence_id"));
      }
    }
    result.push_back(std::move(selection));
  }
  return result;
}

std::unordered_set<std::string> SubjectKeys(const std::vector<Subject>& subjects) {
  std::unordered_set<std::string> result;
  for (const auto& subject : subjects) result.insert(subject.dictionary_key.value_or(subject.normalized));
  return result;
}

bool Aligned(const Requirement& requirement, const Evidence& evidence) {
  const auto lhs = SubjectKeys(requirement.subjects);
  const auto rhs = SubjectKeys(evidence.subjects);
  if (!lhs.empty()) {
    for (const auto& value : lhs) if (rhs.contains(value)) return true;
    return false;
  }
  if (requirement.activity.has_value() && evidence.activity.has_value()) {
    return Normalize(*requirement.activity) == Normalize(*evidence.activity);
  }
  return false;
}

bool ActorDecisive(const Requirement& requirement, const Evidence& evidence) {
  if (evidence.actor == "self") return true;
  return evidence.actor == "project" && evidence.context == "project" &&
         (requirement.kind == "technology" || requirement.kind == "responsibility");
}

bool StrengthDecisive(const Evidence& evidence) {
  return evidence.strength == "direct" || evidence.strength == "supported";
}

std::int64_t DaysFromCivil(int year, unsigned month, unsigned day) {
  year -= month <= 2;
  const int era = (year >= 0 ? year : year - 399) / 400;
  const unsigned yoe = static_cast<unsigned>(year - era * 400);
  const unsigned doy = (153 * (month + (month > 2 ? static_cast<unsigned>(-3) : 9)) + 2) / 5 + day - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return static_cast<std::int64_t>(era) * 146097 + static_cast<std::int64_t>(doe) - 719468;
}

std::int64_t ParseDay(const std::string& value) {
  if (value.size() != 10 || value[4] != '-' || value[7] != '-') Fail("date must use YYYY-MM-DD");
  try {
    const int year = std::stoi(value.substr(0, 4));
    const unsigned month = static_cast<unsigned>(std::stoi(value.substr(5, 2)));
    const unsigned day = static_cast<unsigned>(std::stoi(value.substr(8, 2)));
    if (month < 1 || month > 12 || day < 1 || day > 31) Fail("invalid date");
    return DaysFromCivil(year, month, day);
  } catch (const std::exception&) {
    Fail("invalid date");
  }
}

std::pair<Decimal, bool> DurationYears(const std::vector<const Evidence*>& evidence, const std::string& as_of) {
  std::vector<std::pair<std::int64_t, std::int64_t>> intervals;
  bool incomplete = false;
  for (const Evidence* item : evidence) {
    if (!item->time_span.has_value() || !item->time_span->start.has_value()) {
      incomplete = true;
      continue;
    }
    std::optional<std::string> end = item->time_span->end;
    if (!end.has_value() && item->time_span->current.value_or(false)) end = as_of;
    if (!end.has_value()) {
      incomplete = true;
      continue;
    }
    const auto start_day = ParseDay(*item->time_span->start);
    const auto end_day = ParseDay(*end);
    if (end_day < start_day) {
      incomplete = true;
      continue;
    }
    intervals.emplace_back(start_day, end_day);
  }
  if (intervals.empty()) return {kZero, incomplete};
  std::sort(intervals.begin(), intervals.end());
  std::vector<std::pair<std::int64_t, std::int64_t>> merged;
  for (const auto& [start, end] : intervals) {
    if (merged.empty() || start > merged.back().second) {
      merged.emplace_back(start, end);
    } else if (end > merged.back().second) {
      merged.back().second = end;
    }
  }
  std::int64_t total = 0;
  for (const auto& [start, end] : merged) total += end - start;
  return {Decimal(total) / kDaysPerYear, incomplete};
}

bool SameSetAndLength(const std::vector<std::string>& lhs, const std::vector<std::string>& rhs) {
  if (lhs.size() != rhs.size()) return false;
  return std::set<std::string>(lhs.begin(), lhs.end()) == std::set<std::string>(rhs.begin(), rhs.end());
}

bool SelectionComplete(const Selection& selection, const std::vector<Evidence>& evidence) {
  std::vector<std::string> all_ids;
  for (const auto& item : evidence) all_ids.push_back(item.id);
  if (all_ids.empty()) return selection.state == "no_evidence";
  return selection.state == "ranked" && SameSetAndLength(selection.pool_ids, all_ids) &&
         SameSetAndLength(selection.candidate_ids, all_ids);
}

Qualification QualifyOne(const Requirement& requirement, const Selection& selection,
                         const std::vector<Evidence>& evidence, const std::string& as_of) {
  std::unordered_map<std::string, const Evidence*> by_id;
  for (const auto& item : evidence) by_id.emplace(item.id, &item);
  for (const auto& id : selection.pool_ids) if (!by_id.contains(id)) Fail("P2-05 candidate pool references unknown evidence");
  for (const auto& id : selection.candidate_ids) if (!by_id.contains(id)) Fail("P2-05 candidate references unknown evidence");

  Qualification result;
  result.requirement_id = requirement.id;
  result.ranked_ids = selection.candidate_ids;
  result.selection_complete = SelectionComplete(selection, evidence);

  if (selection.state == "no_evidence") {
    if (!evidence.empty()) Fail("NO_EVIDENCE selection inconsistent with non-empty corpus");
    result.state = "not_evidenced";
    result.selection_complete = true;
    result.reasons = {"evidence.corpus_empty"};
    return result;
  }
  if (selection.state != "ranked") Fail("applicable requirement must have ranked or no_evidence selection");

  std::vector<const Evidence*> supporting;
  std::vector<const Evidence*> contradicting;
  std::vector<const Evidence*> ambiguous;
  for (const auto& id : selection.candidate_ids) {
    const Evidence* item = by_id.at(id);
    if (!Aligned(requirement, *item)) continue;
    if (ActorDecisive(requirement, *item) && StrengthDecisive(*item)) {
      if (item->polarity == requirement.polarity) supporting.push_back(item);
      else contradicting.push_back(item);
    } else {
      ambiguous.push_back(item);
    }
  }
  for (const auto* item : supporting) result.supporting_ids.push_back(item->id);
  for (const auto* item : contradicting) result.contradicting_ids.push_back(item->id);
  for (const auto* item : ambiguous) result.ambiguous_ids.push_back(item->id);

  if (!supporting.empty() && !contradicting.empty()) {
    result.state = "unknown";
    result.reasons = {"requirements.source_conflict"};
    return result;
  }
  if (!contradicting.empty()) {
    result.state = "contradicted";
    result.upper = kZero;
    result.reasons = {"requirements.explicit_contradiction"};
    return result;
  }
  if (!supporting.empty()) {
    if (requirement.threshold.has_value()) {
      const auto [years, incomplete] = DurationYears(supporting, as_of);
      if (requirement.threshold->minimum.has_value() && years < *requirement.threshold->minimum) {
        if (incomplete) {
          result.state = "unknown";
          result.reasons = {"evidence.date_scope_unresolved"};
          return result;
        }
        if (!result.selection_complete) {
          result.state = "unknown";
          result.reasons = {"evidence.selection_incomplete"};
          return result;
        }
        result.state = "not_evidenced";
        result.reasons = {"requirements.threshold_not_evidenced"};
        return result;
      }
      if (requirement.threshold->maximum.has_value() && years > *requirement.threshold->maximum) {
        result.state = "unknown";
        result.reasons = {"requirements.threshold_scope_unresolved"};
        return result;
      }
    }
    result.state = "matched";
    result.lower = kOne;
    result.upper = kOne;
    result.reasons = {"requirements.direct_support"};
    return result;
  }
  if (!ambiguous.empty()) {
    result.state = "unknown";
    result.reasons = {"evidence.scope_unresolved"};
    return result;
  }
  if (!result.selection_complete) {
    result.state = "unknown";
    result.reasons = {"evidence.selection_incomplete"};
    return result;
  }
  if (!requirement.subjects.empty() || requirement.activity.has_value()) {
    result.state = "not_evidenced";
    result.reasons = {"requirements.subject_not_evidenced"};
    return result;
  }
  result.state = "unknown";
  result.reasons = {"requirements.semantic_alignment_unresolved"};
  return result;
}

QualificationResult Qualify(const Struct& request) {
  const Struct& requirement_set = AsStruct(Require(request, "requirement_set"), "requirement_set");
  const Struct& evidence_set = AsStruct(Require(request, "resume_evidence_set"), "resume_evidence_set");
  const Struct& candidate_set = AsStruct(Require(request, "evidence_candidate_set"), "evidence_candidate_set");
  const auto requirements = ParseRequirements(requirement_set);
  const auto groups = ParseGroups(requirement_set);
  const auto evidence = ParseEvidence(evidence_set);
  const auto selections = ParseSelections(candidate_set);

  std::unordered_map<std::string, Selection> selection_by_id;
  for (const auto& item : selections) {
    if (!selection_by_id.emplace(item.requirement_id, item).second) Fail("duplicate selection requirement id");
  }
  if (selection_by_id.size() != requirements.size()) Fail("candidate set must contain exactly one selection per requirement");
  for (const auto& requirement : requirements) if (!selection_by_id.contains(requirement.id)) Fail("candidate set misses requirement");

  QualificationResult result;
  const std::string as_of = StringField(request, "as_of");
  for (const auto& requirement : requirements) {
    const Selection& selection = selection_by_id.at(requirement.id);
    if (requirement.modality == "not_required") {
      if (selection.state != "skipped_not_required") Fail("not_required must be skipped by P2-05");
      result.ignored.push_back(requirement.id);
      continue;
    }
    result.evaluations.push_back(QualifyOne(requirement, selection, evidence, as_of));
  }

  std::unordered_map<std::string, Qualification> eval_by_id;
  for (const auto& item : result.evaluations) eval_by_id.emplace(item.requirement_id, item);
  std::unordered_set<std::string> ignored(result.ignored.begin(), result.ignored.end());
  std::unordered_map<std::string, Group> group_by_id;
  for (const auto& item : groups) group_by_id.emplace(item.id, item);
  std::unordered_map<std::string, GroupQualification> memo;

  std::function<GroupQualification(const std::string&)> evaluate = [&](const std::string& group_id) -> GroupQualification {
    if (memo.contains(group_id)) return memo.at(group_id);
    if (!group_by_id.contains(group_id)) Fail("unknown group id");
    const Group& group = group_by_id.at(group_id);
    GroupQualification out;
    out.group_id = group.id;
    if (group.op == "conditional") {
      out.reasons = {"requirements.condition_unresolved"};
      memo.emplace(group.id, out);
      return out;
    }
    std::vector<std::pair<Decimal, Decimal>> bounds;
    for (const auto& id : group.requirement_ids) {
      if (ignored.contains(id)) continue;
      if (!eval_by_id.contains(id)) Fail("group references missing qualification");
      const auto& item = eval_by_id.at(id);
      bounds.emplace_back(item.lower, item.upper);
    }
    for (const auto& child : group.child_group_ids) {
      const auto item = evaluate(child);
      bounds.emplace_back(item.lower, item.upper);
    }
    if (bounds.empty()) {
      out.lower = kOne;
      out.upper = kOne;
    } else if (group.op == "all") {
      out.lower = bounds.front().first;
      out.upper = bounds.front().second;
      for (const auto& bound : bounds) {
        out.lower = std::min(out.lower, bound.first);
        out.upper = std::min(out.upper, bound.second);
      }
    } else if (group.op == "any") {
      out.lower = bounds.front().first;
      out.upper = bounds.front().second;
      for (const auto& bound : bounds) {
        out.lower = std::max(out.lower, bound.first);
        out.upper = std::max(out.upper, bound.second);
      }
    } else {
      Fail("unsupported requirement group operator");
    }
    memo.emplace(group.id, out);
    return out;
  };

  for (const auto& group : groups) (void)evaluate(group.id);
  for (const auto& group : groups) result.groups.push_back(memo.at(group.id));
  return result;
}

Struct Support(const Decimal& lower, const Decimal& upper) {
  Struct result;
  PutString(&result, "lower", DecimalString(lower));
  PutString(&result, "upper", DecimalString(upper));
  return result;
}

Struct QualificationStruct(const QualificationResult& result, const Struct& request) {
  Struct output;
  PutString(&output, "schema_version", "careerops.processing.requirement-qualification-set.v1");
  PutString(&output, "input_fingerprint", StringField(request, "input_fingerprint"));
  PutString(&output, "requirement_set_sha256", StringField(request, "requirement_set_sha256"));
  PutString(&output, "resume_evidence_set_sha256", StringField(request, "resume_evidence_set_sha256"));
  PutString(&output, "evidence_candidate_set_sha256", StringField(request, "evidence_candidate_set_sha256"));
  PutString(&output, "qualification_version", StringField(request, "qualification_version"));
  std::vector<Struct> evaluations;
  for (const auto& item : result.evaluations) {
    Struct value;
    PutString(&value, "requirement_id", item.requirement_id);
    PutString(&value, "state", item.state);
    PutStruct(&value, "support", Support(item.lower, item.upper));
    PutBool(&value, "selection_complete", item.selection_complete);
    PutStrings(&value, "ranked_evidence_ids", item.ranked_ids);
    PutStrings(&value, "supporting_evidence_ids", item.supporting_ids);
    PutStrings(&value, "contradicting_evidence_ids", item.contradicting_ids);
    PutStrings(&value, "ambiguous_evidence_ids", item.ambiguous_ids);
    PutStrings(&value, "reason_codes", item.reasons);
    evaluations.push_back(std::move(value));
  }
  PutStructs(&output, "evaluations", evaluations);
  std::vector<Struct> groups;
  for (const auto& item : result.groups) {
    Struct value;
    PutString(&value, "group_id", item.group_id);
    PutStruct(&value, "support", Support(item.lower, item.upper));
    PutStrings(&value, "reason_codes", item.reasons);
    groups.push_back(std::move(value));
  }
  PutStructs(&output, "groups", groups);
  PutStrings(&output, "ignored_requirement_ids", result.ignored);
  return output;
}

struct ScoringUnit {
  std::vector<std::string> requirement_ids;
  Decimal lower{kZero};
  Decimal upper{kOne};
  std::string importance;
  std::string kind;
};

struct Component {
  std::string key;
  Decimal lower{kZero};
  Decimal upper{kHundred};
  Decimal weight{kZero};
};

std::vector<std::string> LeafIds(const Group& group, const std::unordered_map<std::string, Group>& groups) {
  std::vector<std::string> result = group.requirement_ids;
  for (const auto& child : group.child_group_ids) {
    const auto nested = LeafIds(groups.at(child), groups);
    result.insert(result.end(), nested.begin(), nested.end());
  }
  return result;
}

std::string Derived(const std::vector<std::string>& values, const std::string& fallback) {
  if (values.empty()) return fallback;
  for (const auto& value : values) if (value != values.front()) return fallback;
  return values.front();
}

std::vector<ScoringUnit> ScoringUnits(const std::vector<Requirement>& requirements,
                                      const std::vector<Group>& groups,
                                      const QualificationResult& qualification,
                                      const Struct& requirement_set) {
  const auto root = OptionalString(requirement_set, "root_group_id");
  if (!root.has_value()) return {};
  std::unordered_map<std::string, Requirement> reqs;
  for (const auto& item : requirements) reqs.emplace(item.id, item);
  std::unordered_map<std::string, Group> group_by_id;
  for (const auto& item : groups) group_by_id.emplace(item.id, item);
  std::unordered_map<std::string, Qualification> evals;
  for (const auto& item : qualification.evaluations) evals.emplace(item.requirement_id, item);
  std::unordered_map<std::string, GroupQualification> group_support;
  for (const auto& item : qualification.groups) group_support.emplace(item.group_id, item);
  std::unordered_set<std::string> ignored(qualification.ignored.begin(), qualification.ignored.end());

  std::function<std::vector<ScoringUnit>(const Group&)> collect = [&](const Group& group) {
    std::vector<ScoringUnit> result;
    if (group.op != "all") {
      auto leaves = LeafIds(group, group_by_id);
      leaves.erase(std::remove_if(leaves.begin(), leaves.end(), [&](const std::string& id) { return ignored.contains(id); }), leaves.end());
      if (leaves.empty()) return result;
      std::vector<std::string> importances;
      std::vector<std::string> kinds;
      for (const auto& id : leaves) {
        importances.push_back(reqs.at(id).importance);
        kinds.push_back(reqs.at(id).kind);
      }
      const auto support = group_support.at(group.id);
      result.push_back({leaves, support.lower, support.upper, Derived(importances, "unknown"), Derived(kinds, "other")});
      return result;
    }
    for (const auto& id : group.requirement_ids) {
      if (ignored.contains(id)) continue;
      const auto& req = reqs.at(id);
      const auto& eval = evals.at(id);
      result.push_back({{id}, eval.lower, eval.upper, req.importance, req.kind});
    }
    for (const auto& child : group.child_group_ids) {
      auto nested = collect(group_by_id.at(child));
      result.insert(result.end(), nested.begin(), nested.end());
    }
    return result;
  };
  return collect(group_by_id.at(*root));
}

std::optional<std::pair<Decimal, Decimal>> AverageBounds(const std::vector<const ScoringUnit*>& units) {
  if (units.empty()) return std::nullopt;
  Decimal lower = kZero;
  Decimal upper = kZero;
  for (const auto* item : units) { lower += item->lower; upper += item->upper; }
  const Decimal count(static_cast<unsigned>(units.size()));
  return std::make_pair(lower / count * kHundred, upper / count * kHundred);
}

std::map<std::string, std::vector<const ScoringUnit*>> ComponentUnits(const std::vector<ScoringUnit>& units) {
  std::map<std::string, std::vector<const ScoringUnit*>> result;
  const std::vector<std::string> keys = {"mandatory_coverage", "preferred_coverage", "optional_coverage", "technology_fit", "responsibility_fit", "experience_fit", "domain_fit", "education_fit", "language_fit", "work_condition_fit", "other_fit"};
  for (const auto& key : keys) result[key] = {};
  for (const auto& item : units) {
    if (item.importance == "mandatory") result["mandatory_coverage"].push_back(&item);
    if (item.importance == "preferred") result["preferred_coverage"].push_back(&item);
    if (item.importance == "optional") result["optional_coverage"].push_back(&item);
    if (item.kind == "technology") result["technology_fit"].push_back(&item);
    else if (item.kind == "responsibility") result["responsibility_fit"].push_back(&item);
    else if (item.kind == "experience") result["experience_fit"].push_back(&item);
    else if (item.kind == "domain") result["domain_fit"].push_back(&item);
    else if (item.kind == "education") result["education_fit"].push_back(&item);
    else if (item.kind == "language") result["language_fit"].push_back(&item);
    else if (item.kind == "work_condition") result["work_condition_fit"].push_back(&item);
    else if (item.kind == "other") result["other_fit"].push_back(&item);
  }
  return result;
}

std::set<std::string> RoleFamilies(const std::string& raw) {
  const std::string title = Normalize(raw);
  std::set<std::string> out;
  auto any = [&](std::initializer_list<std::string_view> values) {
    for (const auto value : values) if (Contains(title, value)) return true;
    return false;
  };
  if (any({"data engineer", "data-engineer", "data engineering", "etl engineer", "dwh engineer", "инженер данных", "дата-инженер"})) out.insert("data_engineering");
  if (any({"machine learning engineer", "ml engineer", "ml-engineer", "ml разработчик", "ml-инженер"})) out.insert("ml_engineering");
  if (any({"data scientist", "дата-сайентист", "дата сайентист"})) out.insert("data_science");
  if (any({"llm engineer", "nlp engineer", "rag engineer", "ai engineer", "genai engineer", "ии инженер"})) out.insert("ai_llm");
  if (any({"computer vision", "cv engineer", "vlm engineer"})) out.insert("computer_vision");
  if (Contains(title, "mlops")) out.insert("mlops");
  if (any({"research engineer", "research scientist", "ml researcher", "ai researcher"})) out.insert("ml_research");
  if (any({"recommendation", "recommender", "ranking engineer", "personalization"})) out.insert("recommendation_ranking");
  if (any({"python backend", "backend developer python", "backend engineer python", "python бэкенд", "python бекенд"})) out.insert("python_backend");
  if (any({"c++ developer", "c++ engineer", "c++ programmer", "разработчик c++", "программист c++"})) out.insert("cpp");
  if (any({"data analyst", "аналитик данных", "bi analyst"})) out.insert("data_analytics");
  if (any({"java backend", "backend developer java", "backend engineer java", "java бэкенд", "java бекенд"})) out.insert("java_backend");
  if (any({"frontend", "front-end", "фронтенд"})) out.insert("frontend");
  if (Contains(title, "devops")) out.insert("devops");
  if (any({"qa engineer", "test engineer", "тестировщик"})) out.insert("qa");
  if (any({"product manager", "продуктовый менеджер"})) out.insert("product_management");
  if (any({"project manager", "руководитель проекта"})) out.insert("project_management");
  if (any({"business analyst", "бизнес-аналитик"})) out.insert("business_analysis");
  if (any({"system analyst", "systems analyst", "системный аналитик"})) out.insert("system_analysis");
  if (any({"database administrator", " dba", "dba ", "администратор баз"})) out.insert("dba");
  return out;
}

std::set<std::string> SeniorityLevels(const std::string& raw) {
  const std::string title = Normalize(raw);
  std::set<std::string> out;
  if (Contains(title, "intern") || Contains(title, "trainee") || Contains(title, "стажер")) out.insert("intern");
  if (Contains(title, "junior") || Contains(title, " jr") || Contains(title, "младший")) out.insert("junior");
  if (Contains(title, "middle") || Contains(title, " mid") || Contains(title, "мидл")) out.insert("middle");
  if (Contains(title, "senior") || Contains(title, " sr") || Contains(title, "старший")) out.insert("senior");
  if (Contains(title, "lead") || Contains(title, "лид") || Contains(title, "тимлид")) out.insert("lead");
  if (Contains(title, "principal") || Contains(title, "staff") || Contains(title, "ведущий")) out.insert("principal");
  if (Contains(title, "head") || Contains(title, "director") || Contains(title, "руководитель")) out.insert("head");
  return out;
}

bool IsSubset(const std::set<std::string>& lhs, const std::set<std::string>& rhs) {
  return std::includes(rhs.begin(), rhs.end(), lhs.begin(), lhs.end());
}

bool Disjoint(const std::set<std::string>& lhs, const std::set<std::string>& rhs) {
  for (const auto& value : lhs) if (rhs.contains(value)) return false;
  return true;
}

std::set<std::string> StringSet(const Struct& object, std::string_view key) {
  const auto values = StringList(object, key);
  return {values.begin(), values.end()};
}

std::optional<std::string> KnownSourceString(const Struct& source_value) {
  if (StringField(source_value, "state") != "known") return std::nullopt;
  return OptionalString(source_value, "value");
}

std::pair<std::set<std::string>, bool> WorkFormats(const Struct& vacancy) {
  std::set<std::string> formats;
  const Value* value = Find(vacancy, "work_formats");
  if (value == nullptr) return {formats, false};
  const auto& list = AsList(*value, "work_formats");
  if (list.values().empty()) return {formats, false};
  for (const auto& item : list.values()) {
    const Struct& label = AsStruct(item, "work_format");
    std::string text = StringField(label, "key") + " ";
    if (const auto v = OptionalString(label, "label")) text += *v + " ";
    if (const auto v = OptionalString(label, "source_code")) text += *v;
    text = Normalize(text);
    if (Contains(text, "remote") || Contains(text, "удален") || Contains(text, "дистанц")) formats.insert("remote");
    else if (Contains(text, "hybrid") || Contains(text, "гибрид")) formats.insert("hybrid");
    else if (Contains(text, "onsite") || Contains(text, "on-site") || Contains(text, "office") || Contains(text, "офис") || Contains(text, "на месте")) formats.insert("onsite");
    else return {formats, false};
  }
  return {formats, true};
}

std::map<std::string, std::pair<Decimal, Decimal>> StructuredBounds(const Struct* vacancy, const Struct& policy_content) {
  std::map<std::string, std::pair<Decimal, Decimal>> result;
  if (vacancy == nullptr) return result;
  const Value* filtering_value = Find(policy_content, "filtering");
  Struct empty;
  const Struct& filtering = filtering_value == nullptr ? empty : AsStruct(*filtering_value, "filtering");
  const auto allowed_roles = StringSet(filtering, "allowed_primary_roles");
  const auto forbidden_roles = StringSet(filtering, "forbidden_primary_roles");
  const auto forbidden_seniority = StringSet(filtering, "forbidden_seniority");
  const auto allowed_formats = StringSet(filtering, "allowed_work_formats");
  const auto allowed_area_ids = StringSet(filtering, "allowed_area_ids");
  const auto allowed_area_names = StringSet(filtering, "allowed_area_names");

  const Struct* title_source = nullptr;
  if (const Value* title = Find(*vacancy, "title"); title != nullptr && title->kind_case() == Value::kStructValue) title_source = &title->struct_value();
  std::optional<std::string> title;
  if (title_source != nullptr) title = KnownSourceString(*title_source);
  if (!allowed_roles.empty() || !forbidden_roles.empty()) {
    if (!title.has_value()) result["role_fit"] = {kZero, kHundred};
    else {
      const auto families = RoleFamilies(*title);
      if (families.empty()) result["role_fit"] = {kZero, kHundred};
      else if (!allowed_roles.empty()) {
        if (IsSubset(families, allowed_roles)) result["role_fit"] = {kHundred, kHundred};
        else if (Disjoint(families, allowed_roles)) result["role_fit"] = {kZero, kZero};
        else result["role_fit"] = {kZero, kHundred};
      } else if (IsSubset(families, forbidden_roles)) result["role_fit"] = {kZero, kZero};
      else if (Disjoint(families, forbidden_roles)) result["role_fit"] = {kHundred, kHundred};
      else result["role_fit"] = {kZero, kHundred};
    }
  }
  if (!forbidden_seniority.empty()) {
    if (!title.has_value()) result["seniority_fit"] = {kZero, kHundred};
    else {
      const auto levels = SeniorityLevels(*title);
      if (levels.empty()) result["seniority_fit"] = {kZero, kHundred};
      else if (IsSubset(levels, forbidden_seniority)) result["seniority_fit"] = {kZero, kZero};
      else if (Disjoint(levels, forbidden_seniority)) result["seniority_fit"] = {kHundred, kHundred};
      else result["seniority_fit"] = {kZero, kHundred};
    }
  }

  const auto [formats, formats_complete] = WorkFormats(*vacancy);
  if (!allowed_formats.empty()) {
    if (!Disjoint(formats, allowed_formats)) result["work_format_fit"] = {kHundred, kHundred};
    else if (!formats.empty() && formats_complete) result["work_format_fit"] = {kZero, kZero};
    else result["work_format_fit"] = {kZero, kHundred};
  }
  if (!allowed_area_ids.empty() || !allowed_area_names.empty()) {
    if (formats.contains("remote")) result["location_fit"] = {kHundred, kHundred};
    else if (!formats_complete) result["location_fit"] = {kZero, kHundred};
    else {
      const Value* location_value = Find(*vacancy, "location");
      if (location_value == nullptr || location_value->kind_case() != Value::kStructValue || StringField(location_value->struct_value(), "state") != "known") {
        result["location_fit"] = {kZero, kHundred};
      } else {
        const Value* raw = Find(location_value->struct_value(), "value");
        if (raw == nullptr || raw->kind_case() != Value::kStructValue) result["location_fit"] = {kZero, kHundred};
        else {
          const Struct& location = raw->struct_value();
          const auto id = OptionalString(location, "area_id");
          const auto name = OptionalString(location, "area_name");
          bool matched = false;
          bool complete = true;
          if (!allowed_area_ids.empty()) {
            if (!id.has_value()) complete = false;
            else matched = allowed_area_ids.contains(Normalize(*id));
          }
          if (!allowed_area_names.empty()) {
            if (!name.has_value()) complete = false;
            else {
              const std::string normalized = Normalize(*name);
              for (const auto& allowed : allowed_area_names) if (Normalize(allowed) == normalized) matched = true;
            }
          }
          if (matched) result["location_fit"] = {kHundred, kHundred};
          else if (complete) result["location_fit"] = {kZero, kZero};
          else result["location_fit"] = {kZero, kHundred};
        }
      }
    }
  }
  return result;
}

const std::set<std::string> kComponentKeys = {"role_fit", "mandatory_coverage", "preferred_coverage", "optional_coverage", "responsibility_fit", "technology_fit", "experience_fit", "seniority_fit", "domain_fit", "location_fit", "work_format_fit", "education_fit", "language_fit", "work_condition_fit", "other_fit"};
const std::set<std::string> kCoverageKeys = {"mandatory_coverage", "preferred_coverage", "optional_coverage"};
const std::set<std::string> kKindKeys = {"responsibility_fit", "technology_fit", "experience_fit", "domain_fit", "education_fit", "language_fit", "work_condition_fit", "other_fit"};
const std::vector<std::string> kComponentOrder = {"mandatory_coverage", "preferred_coverage", "optional_coverage", "technology_fit", "responsibility_fit", "experience_fit", "domain_fit", "education_fit", "language_fit", "work_condition_fit", "other_fit"};

std::map<std::string, Decimal> ParseWeights(const Struct& scoring) {
  const Struct& weights = AsStruct(Require(scoring, "component_weights"), "component_weights");
  if (weights.fields().empty()) Fail("scoring component weights must not be empty");
  std::map<std::string, Decimal> result;
  Decimal total = kZero;
  bool has_coverage = false;
  bool has_kind = false;
  for (const auto& [key, value] : weights.fields()) {
    if (!kComponentKeys.contains(key)) Fail("unsupported scoring component");
    const Decimal weight = ParseDecimal(AsString(value, "component weight"), "component weight");
    if (weight < 0) Fail("negative scoring weight");
    if (weight > 0 && kCoverageKeys.contains(key)) has_coverage = true;
    if (weight > 0 && kKindKeys.contains(key)) has_kind = true;
    total += weight;
    result.emplace(key, weight);
  }
  if (total <= 0) Fail("scoring weights must contain positive weight");
  if (has_coverage && has_kind) Fail("scoring weights double-count requirement support");
  return result;
}

std::vector<Component> BuildComponents(const std::map<std::string, std::vector<const ScoringUnit*>>& unit_map,
                                       const std::map<std::string, std::pair<Decimal, Decimal>>& structured,
                                       const std::optional<std::map<std::string, Decimal>>& weights) {
  std::vector<Component> result;
  std::set<std::string> present;
  for (const auto& key : kComponentOrder) {
    const auto bounds = AverageBounds(unit_map.at(key));
    if (!bounds.has_value()) continue;
    const Decimal weight = weights.has_value() && weights->contains(key) ? weights->at(key) : kZero;
    result.push_back({key, bounds->first, bounds->second, weight});
    present.insert(key);
  }
  for (const auto key : {std::string("role_fit"), std::string("seniority_fit"), std::string("work_format_fit"), std::string("location_fit")}) {
    if (!structured.contains(key)) continue;
    const Decimal weight = weights.has_value() && weights->contains(key) ? weights->at(key) : kZero;
    result.push_back({key, structured.at(key).first, structured.at(key).second, weight});
    present.insert(key);
  }
  if (weights.has_value()) {
    for (const auto& [key, weight] : *weights) {
      if (weight > 0 && !present.contains(key)) result.push_back({key, kZero, kHundred, weight});
    }
  }
  return result;
}

std::optional<std::pair<Decimal, Decimal>> WeightedScore(const std::vector<Component>& components) {
  Decimal total_weight = kZero;
  Decimal lower = kZero;
  Decimal upper = kZero;
  for (const auto& item : components) {
    if (item.weight <= 0) continue;
    total_weight += item.weight;
    lower += item.lower * item.weight;
    upper += item.upper * item.weight;
  }
  if (total_weight <= 0) return std::nullopt;
  return std::make_pair(lower / total_weight, upper / total_weight);
}

std::pair<Decimal, Decimal> MandatorySupport(const std::vector<ScoringUnit>& units) {
  Decimal lower = kZero;
  Decimal upper = kZero;
  std::size_t count = 0;
  for (const auto& item : units) {
    if (item.importance != "mandatory") continue;
    lower += item.lower;
    upper += item.upper;
    ++count;
  }
  if (count == 0) return {kOne, kOne};
  const Decimal denominator(static_cast<unsigned>(count));
  return {lower / denominator, upper / denominator};
}

Struct ComponentStruct(const Component& item) {
  Struct value;
  PutString(&value, "key", item.key);
  PutString(&value, "lower", DecimalString(item.lower));
  PutString(&value, "upper", DecimalString(item.upper));
  PutString(&value, "weight", DecimalString(item.weight));
  return value;
}

Struct DecisionStruct(const Struct& request, const QualificationResult& qualification) {
  const Struct& requirement_set = AsStruct(Require(request, "requirement_set"), "requirement_set");
  const auto requirements = ParseRequirements(requirement_set);
  const auto groups = ParseGroups(requirement_set);
  const auto units = ScoringUnits(requirements, groups, qualification, requirement_set);
  const auto unit_map = ComponentUnits(units);
  const auto mandatory = MandatorySupport(units);
  const Struct& policy_content = AsStruct(Require(request, "target_policy_content"), "target_policy_content");
  const Value* vacancy_value = Find(request, "vacancy");
  const Struct* vacancy = vacancy_value != nullptr && vacancy_value->kind_case() == Value::kStructValue ? &vacancy_value->struct_value() : nullptr;
  const auto structured = StructuredBounds(vacancy, policy_content);

  std::unordered_map<std::string, Requirement> requirement_by_id;
  for (const auto& item : requirements) requirement_by_id.emplace(item.id, item);
  std::vector<std::string> critical;
  std::vector<std::string> unknown;
  std::vector<std::string> not_evidenced;
  for (const auto& item : qualification.evaluations) {
    if (item.state == "unknown") unknown.push_back(item.requirement_id);
    if (item.state == "not_evidenced") not_evidenced.push_back(item.requirement_id);
    if (item.state == "contradicted" && requirement_by_id.at(item.requirement_id).modality == "prohibited") critical.push_back(item.requirement_id);
  }

  const std::string calibration = StringField(request, "calibration_version");
  std::string decision;
  std::pair<Decimal, Decimal> score{kZero, kHundred};
  std::vector<Component> components;
  std::vector<std::string> reasons;

  if (!critical.empty()) {
    decision = "skip";
    score = {kZero, kZero};
    components = BuildComponents(unit_map, structured, std::nullopt);
    reasons = {"requirements.critical_contradiction"};
  } else if (calibration == "calibration-unset") {
    decision = "review";
    score = {mandatory.first * kHundred, mandatory.second * kHundred};
    components = BuildComponents(unit_map, structured, std::nullopt);
    reasons = {"match.calibration_unset"};
  } else {
    const Value* scoring_value = Find(policy_content, "scoring");
    if (scoring_value == nullptr || scoring_value->kind_case() != Value::kStructValue) Fail("calibrated P2-07 requires scoring policy");
    const Struct& scoring = scoring_value->struct_value();
    if (StringField(scoring, "calibration_version") != calibration) Fail("scoring calibration version mismatch");
    const Decimal candidate_threshold = ParseDecimal(StringField(scoring, "candidate_min_score"), "candidate_min_score");
    const Decimal mandatory_threshold = ParseDecimal(StringField(scoring, "mandatory_min_support"), "mandatory_min_support");
    if (candidate_threshold < 0 || candidate_threshold > 100 || mandatory_threshold < 0 || mandatory_threshold > 1) Fail("scoring threshold out of range");
    const auto weights = ParseWeights(scoring);
    const Value* ttl = Find(scoring, "candidate_ttl_seconds");
    if (ttl == nullptr || ttl->kind_case() != Value::kNumberValue || ttl->number_value() <= 0) Fail("candidate_ttl_seconds must be positive");
    components = BuildComponents(unit_map, structured, weights);
    const auto weighted = WeightedScore(components);
    if (!weighted.has_value()) {
      decision = "review";
      score = {kZero, kHundred};
      reasons = {"match.scoring_signal_unavailable"};
    } else {
      score = *weighted;
      if (mandatory.second < mandatory_threshold || score.second < candidate_threshold) {
        decision = "skip";
        reasons = {"match.policy_not_satisfied"};
      } else if (mandatory.first >= mandatory_threshold && score.first >= candidate_threshold) {
        decision = "application_candidate";
        reasons = {"match.policy_satisfied"};
      } else {
        decision = "review";
        reasons = {"match.policy_uncertain"};
        for (const auto& id : not_evidenced) if (requirement_by_id.at(id).importance == "mandatory") { reasons = {"requirements.mandatory_not_evidenced"}; break; }
        if (reasons.front() == "match.policy_uncertain") {
          for (const auto& id : unknown) if (requirement_by_id.at(id).importance == "mandatory") { reasons = {"evidence.scope_unresolved"}; break; }
        }
      }
    }
  }

  Struct output;
  PutString(&output, "schema_version", "careerops.processing.match-decision.v1");
  PutString(&output, "input_fingerprint", StringField(request, "input_fingerprint"));
  PutNull(&output, "requirement_qualification_set_sha256");
  PutString(&output, "scoring_version", StringField(request, "scoring_version"));
  PutString(&output, "calibration_version", calibration);
  PutString(&output, "policy_version", StringField(request, "policy_version"));
  PutString(&output, "decision", decision);
  PutStruct(&output, "score", Support(score.first, score.second));
  PutString(&output, "deterministic_score", DecimalString(score.first));
  std::vector<Struct> component_values;
  for (const auto& item : components) component_values.push_back(ComponentStruct(item));
  PutStructs(&output, "components", component_values);
  PutStrings(&output, "critical_conflict_requirement_ids", critical);
  PutStrings(&output, "unknown_requirement_ids", unknown);
  PutStrings(&output, "not_evidenced_requirement_ids", not_evidenced);
  PutStrings(&output, "reason_codes", reasons);
  return output;
}

void ValidateProtocol(const Struct& request) {
  if (StringField(request, "protocol_version") != kDecisionProtocolVersion) Fail("unsupported decision protocol version");
}

}  // namespace

Struct EvaluateDecision(const Struct& request) {
  ValidateProtocol(request);
  const auto qualification = Qualify(request);
  Struct response;
  PutString(&response, "protocol_version", kDecisionProtocolVersion);
  PutStruct(&response, "qualification_set", QualificationStruct(qualification, request));
  PutStruct(&response, "decision", DecisionStruct(request, qualification));
  return response;
}

Struct EvaluateBatch(const Struct& request) {
  ValidateProtocol(request);
  const auto& items = AsList(Require(request, "items"), "items");
  Struct response;
  PutString(&response, "protocol_version", kDecisionProtocolVersion);
  auto* output = (*response.mutable_fields())["items"].mutable_list_value();
  for (const auto& item : items.values()) {
    *output->add_values()->mutable_struct_value() = EvaluateDecision(AsStruct(item, "batch item"));
  }
  return response;
}

}  // namespace careerops::matching_core
