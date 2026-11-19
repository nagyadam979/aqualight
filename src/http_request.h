#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>

namespace aqua {

// One bounded HTTP/1 request. References point into the object's own storage;
// nothing is dispatched until the entire declared body has been validated.
class HttpRequest {
 public:
  static constexpr size_t HEADER_LIMIT = 4096;
  static constexpr size_t BODY_LIMIT = 3072;
  static constexpr size_t ARG_LIMIT = 8;
  static constexpr size_t URI_LIMIT = 160;
  static constexpr size_t HEADER_COUNT_LIMIT = 32;
  enum class Method { Get, Post };

  void reset() {
    std::memset(buffer_, 0, sizeof(buffer_));
    used_ = headerBytes_ = contentLength_ = argCount_ = headerCount_ = 0;
    error_ = 0; complete_ = false; method_ = Method::Get; path_ = nullptr;
  }

  size_t feed(const uint8_t *data, size_t length) {
    size_t consumed = 0;
    while (consumed < length && !complete_ && !error_) {
      const char ch = static_cast<char>(data[consumed++]);
      if (ch == '\0') { fail(400); break; }
      if (used_ >= HEADER_LIMIT + BODY_LIMIT) { fail(headerBytes_ ? 413 : 431); break; }
      buffer_[used_++] = ch;
      buffer_[used_] = 0;
      if (!headerBytes_) {
        if (used_ > HEADER_LIMIT) { fail(431); break; }
        if (ch == '\n' && (used_ < 2 || buffer_[used_ - 2] != '\r')) { fail(400); break; }
        if (used_ >= 4 && std::memcmp(buffer_ + used_ - 4, "\r\n\r\n", 4) == 0) {
          headerBytes_ = used_;
          if (!parseHeaders()) break;
          if (!contentLength_) finish();
        }
      } else if (used_ - headerBytes_ == contentLength_) {
        finish();
      }
    }
    return consumed;
  }

  bool complete() const { return complete_; }
  int errorStatus() const { return error_; }
  Method method() const { return method_; }
  const char *path() const { return path_ ? path_ : ""; }
  const char *header(const char *name) const {
    for (size_t i = 0; i < headerCount_; ++i) if (equal(headers_[i].name, name)) return headers_[i].value;
    return "";
  }
  size_t args() const { return argCount_; }
  const char *arg(const char *name) const {
    for (size_t i = 0; i < argCount_; ++i) if (std::strcmp(arguments_[i].name, name) == 0) return arguments_[i].value;
    return "";
  }
  const char *arg(size_t index) const { return index < argCount_ ? arguments_[index].value : ""; }
  const char *arg(int index) const { return index < 0 ? "" : arg(static_cast<size_t>(index)); }
  const char *argName(size_t index) const { return index < argCount_ ? arguments_[index].name : ""; }
  bool hasArg(const char *name) const {
    for (size_t i = 0; i < argCount_; ++i) if (std::strcmp(arguments_[i].name, name) == 0) return true;
    return false;
  }
  static bool equal(const char *a, const char *b) {
    while (*a && *b) {
      const char ca = *a >= 'A' && *a <= 'Z' ? *a + ('a' - 'A') : *a;
      const char cb = *b >= 'A' && *b <= 'Z' ? *b + ('a' - 'A') : *b;
      if (ca != cb) return false;
      ++a; ++b;
    }
    return *a == *b;
  }

 private:
  struct Field { char *name = nullptr; char *value = nullptr; };
  char buffer_[HEADER_LIMIT + BODY_LIMIT + 1] = {};
  Field headers_[HEADER_COUNT_LIMIT];
  Field arguments_[ARG_LIMIT];
  size_t used_ = 0, headerBytes_ = 0, contentLength_ = 0, argCount_ = 0, headerCount_ = 0;
  int error_ = 0;
  bool complete_ = false;
  Method method_ = Method::Get;
  char *path_ = nullptr;

  bool fail(int status) { error_ = status; return false; }
  static bool token(char ch) {
    if ((ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9')) return true;
    return ch && std::strchr("!#$%&'*+-.^_`|~", ch);
  }
  static int hex(char ch) {
    if (ch >= '0' && ch <= '9') return ch - '0';
    if (ch >= 'a' && ch <= 'f') return ch - 'a' + 10;
    if (ch >= 'A' && ch <= 'F') return ch - 'A' + 10;
    return -1;
  }
  bool decode(char *text) {
    char *out = text;
    for (char *in = text; *in; ++in) {
      unsigned char ch = static_cast<unsigned char>(*in);
      if (ch == '+') ch = ' ';
      else if (ch == '%') {
        if (!in[1] || !in[2] || hex(in[1]) < 0 || hex(in[2]) < 0) return fail(400);
        ch = static_cast<unsigned char>((hex(in[1]) << 4) | hex(in[2]));
        in += 2;
        if (!ch) return fail(400);
      }
      *out++ = static_cast<char>(ch);
    }
    *out = 0;
    return true;
  }
  bool parseArguments(char *text) {
    if (!*text) return true;
    for (char *part = text; part;) {
      if (argCount_ == ARG_LIMIT) return fail(413);
      char *next = std::strchr(part, '&');
      if (next) { *next++ = 0; if (!*next) return fail(400); }
      if (!*part) return fail(400);
      char *value = std::strchr(part, '=');
      if (value) *value++ = 0;
      else value = part + std::strlen(part);
      if (!decode(part) || !decode(value) || !*part) return fail(400);
      arguments_[argCount_++] = {part, value};
      part = next;
    }
    return true;
  }
  bool parseHeaders() {
    char *lineEnd = std::strstr(buffer_, "\r\n");
    if (!lineEnd) return fail(400);
    *lineEnd = 0;
    char *uri = std::strchr(buffer_, ' ');
    if (!uri) return fail(400);
    *uri++ = 0;
    char *version = std::strchr(uri, ' ');
    if (!version) return fail(400);
    *version++ = 0;
    if (std::strcmp(buffer_, "GET") == 0) method_ = Method::Get;
    else if (std::strcmp(buffer_, "POST") == 0) method_ = Method::Post;
    else return fail(405);
    if (std::strcmp(version, "HTTP/1.1") != 0 && std::strcmp(version, "HTTP/1.0") != 0) return fail(400);
    if (std::strlen(uri) > URI_LIMIT) return fail(414);
    if (*uri != '/') return fail(400);
    for (const unsigned char *p = reinterpret_cast<unsigned char *>(uri); *p; ++p)
      if (*p <= 0x20 || *p >= 0x7f || *p == '#') return fail(400);
    path_ = uri;
    char *query = std::strchr(path_, '?');
    if (query) { *query++ = 0; if (!parseArguments(query)) return false; }

    bool haveLength = false, haveHost = false;
    for (char *line = lineEnd + 2; line < buffer_ + headerBytes_ - 2;) {
      char *end = std::strstr(line, "\r\n");
      if (!end) return fail(400);
      *end = 0;
      char *colon = std::strchr(line, ':');
      if (!colon || colon == line) return fail(400);
      for (char *p = line; p != colon; ++p) if (!token(*p)) return fail(400);
      *colon = 0;
      char *value = colon + 1;
      while (*value == ' ' || *value == '\t') ++value;
      char *tail = end;
      while (tail > value && (tail[-1] == ' ' || tail[-1] == '\t')) --tail;
      *tail = 0;
      for (const unsigned char *p = reinterpret_cast<unsigned char *>(value); *p; ++p)
        if ((*p < 0x20 && *p != '\t') || *p == 0x7f) return fail(400);
      if (equal(line, "transfer-encoding")) return fail(400);
      if (equal(line, "expect")) return fail(417);
      if (equal(line, "content-length") || equal(line, "content-type") || equal(line, "x-aqua-request") || equal(line, "host")) {
        for (size_t i = 0; i < headerCount_; ++i) if (equal(headers_[i].name, line)) return fail(400);
      }
      if (equal(line, "content-length")) {
        if (haveLength || !*value) return fail(400);
        haveLength = true;
        for (const char *p = value; *p; ++p) {
          if (*p < '0' || *p > '9') return fail(400);
          if (contentLength_ > (BODY_LIMIT - static_cast<size_t>(*p - '0')) / 10) return fail(413);
          contentLength_ = contentLength_ * 10 + static_cast<size_t>(*p - '0');
        }
      }
      if (equal(line, "host")) { if (haveHost || !*value) return fail(400); haveHost = true; }
      if (headerCount_ == HEADER_COUNT_LIMIT) return fail(431);
      headers_[headerCount_++] = {line, value};
      line = end + 2;
    }
    if (std::strcmp(version, "HTTP/1.1") == 0 && !haveHost) return fail(400);
    if (method_ == Method::Get && contentLength_) return fail(400);
    return true;
  }
  void finish() {
    if (contentLength_) {
      const char *type = header("content-type");
      constexpr char encoded[] = "application/x-www-form-urlencoded";
      if (std::strncmp(type, encoded, sizeof(encoded) - 1) == 0 &&
          (!type[sizeof(encoded) - 1] || type[sizeof(encoded) - 1] == ';')) {
        if (!parseArguments(buffer_ + headerBytes_)) return;
      }
    }
    complete_ = !error_;
  }
};

}  // namespace aqua
