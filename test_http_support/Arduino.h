#pragma once
#include <cstddef>
#include <cstdint>
#include <string>
uint32_t millis();
class String {
 public:
  String() = default;
  String(const char *value) : text_(value ? value : "") {}
  size_t length() const { return text_.size(); }
  const char *c_str() const { return text_.c_str(); }
  bool reserve(size_t capacity) { text_.reserve(capacity); return true; }
  String &operator=(const char *value) { text_ = value ? value : ""; return *this; }
  String &operator+=(const char *value) { text_ += value; return *this; }
  String &operator+=(const String &value) { text_ += value.text_; return *this; }
  bool operator==(const char *value) const { return text_ == value; }
 private:
  std::string text_;
};
