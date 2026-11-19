#include "bounded_web_server.h"

#include <lwip/sockets.h>
#include <algorithm>
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <new>

namespace {
bool expired(uint32_t now, uint32_t then, uint32_t interval) { return static_cast<uint32_t>(now - then) >= interval; }
bool retryable() { return errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR; }
bool safeHeader(const char *name, const char *value) {
  if (!name || !*name || !value) return false;
  if (std::strlen(name) > 64 || std::strlen(value) > 256) return false;
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(name); *p; ++p)
    if (*p <= 0x20 || *p >= 0x7f || *p == ':') return false;
  for (const unsigned char *p = reinterpret_cast<const unsigned char *>(value); *p; ++p)
    if ((*p < 0x20 && *p != '\t') || *p == 0x7f) return false;
  return true;
}
}

BoundedWebServer::BoundedWebServer(uint16_t port) : server_(port, CLIENT_LIMIT) {}
BoundedWebServer::~BoundedWebServer() { for (auto &session : sessions_) close(session); }
void BoundedWebServer::begin() { if (configured_) { server_.setNoDelay(true); server_.begin(); } }
BoundedWebServer::operator bool() { return configured_ && static_cast<bool>(server_); }
void BoundedWebServer::on(const char *path, HTTPMethod method, Handler handler) {
  if (!path || !handler || routeCount_ == ROUTE_LIMIT) { configured_ = false; return; }
  routes_[routeCount_].path = path; routes_[routeCount_].method = method;
  if (routes_[routeCount_].path.length() != std::strlen(path)) { configured_ = false; return; }
  routes_[routeCount_++].handler = handler;
}
void BoundedWebServer::onNotFound(Handler handler) { notFound_ = handler; }

void BoundedWebServer::close(Session &s) {
  s.client.stop(); s.client = NetworkClient();
  s.active = s.responding = s.invalidHeaders = false;
  if (s.request && (current_ < 0 || &sessions_[current_] != &s)) { s.request->reset(); s.request.reset(); }
  s.headerCount = 0;
  for (auto &header : s.headers) { header.name = String(); header.value = String(); }
  s.responseHeader = String(); s.ownedBody = String();
  s.body = nullptr; s.bodyLength = s.headerSent = s.bodySent = 0;
}

void BoundedWebServer::handleClient() {
  NetworkClient incoming = server_.accept();
  if (incoming.fd() >= 0) {
    Session *slot = nullptr;
    for (auto &s : sessions_) if (!s.active) { slot = &s; break; }
    if (!slot) incoming.stop();
    else {
      slot->client = incoming; slot->client.setNoDelay(true); slot->active = true;
      slot->started = slot->progress = millis();
    }
  }
  size_t budget = POLL_BYTE_BUDGET;
  for (size_t step = 0; step < CLIENT_LIMIT; ++step) {
    const size_t index = (first_ + step) % CLIENT_LIMIT;
    auto &s = sessions_[index];
    if (!s.active) continue;
    const uint32_t now = millis();
    if (s.responding) {
      if (expired(now, s.started, RESPONSE_TOTAL_MS) || expired(now, s.progress, RESPONSE_IDLE_MS)) { close(s); continue; }
    } else if (expired(now, s.started, REQUEST_TOTAL_MS) || expired(now, s.progress, REQUEST_IDLE_MS)) {
      error(s, 408);
    }
    if (s.active && !s.responding && budget) {
      receive(s, budget);
      if (s.active && s.request && s.request->errorStatus()) error(s, s.request->errorStatus());
      else if (s.active && s.request && s.request->complete()) dispatch(index);
    }
    if (s.active && s.responding && budget) transmit(s, budget);
  }
  first_ = (first_ + 1) % CLIENT_LIMIT;
}

void BoundedWebServer::receive(Session &s, size_t &budget) {
  uint8_t bytes[512];
  const size_t count = std::min(budget, sizeof(bytes));
  const int received = ::recv(s.client.fd(), bytes, count, MSG_DONTWAIT);
  if (received > 0) {
    budget -= static_cast<size_t>(received); s.progress = millis();
    if (!s.request) {
      s.request.reset(new (std::nothrow) aqua::HttpRequest());
      if (!s.request) { close(s); return; }
    }
    s.request->feed(bytes, static_cast<size_t>(received));
  } else if (received == 0 || !retryable()) close(s);
}

void BoundedWebServer::transmit(Session &s, size_t &budget) {
  size_t quota = std::min<size_t>(budget, 1024);
  for (unsigned part = 0; part < 2 && quota && s.active; ++part) {
    const bool header = s.headerSent < s.responseHeader.length();
    const char *data = header ? s.responseHeader.c_str() + s.headerSent : s.body + s.bodySent;
    const size_t remaining = header ? s.responseHeader.length() - s.headerSent : s.bodyLength - s.bodySent;
    if (!remaining) { close(s); return; }
    const size_t count = std::min(quota, remaining);
    const int sent = ::send(s.client.fd(), data, count, MSG_DONTWAIT);
    if (sent > 0) {
      const size_t n = static_cast<size_t>(sent);
      if (header) s.headerSent += n; else s.bodySent += n;
      budget -= n; quota -= n; s.progress = millis();
      if (s.headerSent == s.responseHeader.length() && s.bodySent == s.bodyLength) { close(s); return; }
    } else {
      if (sent == 0 || !retryable()) close(s);
      return;
    }
  }
}

void BoundedWebServer::dispatch(size_t index) {
  current_ = static_cast<int>(index);
  Session &s = sessions_[index];
  bool handled = false;
  for (size_t i = 0; i < routeCount_; ++i) {
    if (routes_[i].path == s.request->path() && routes_[i].method == method()) {
      routes_[i].handler(); handled = true; break;
    }
  }
  if (!handled && notFound_) notFound_();
  if (s.active && !s.responding) error(s, handled ? 500 : 404);
  current_ = -1;
  if (s.request) { s.request->reset(); s.request.reset(); }
}

const char *BoundedWebServer::reason(int code) {
  switch (code) {
    case 200: return "OK"; case 202: return "Accepted"; case 204: return "No Content";
    case 400: return "Bad Request"; case 403: return "Forbidden"; case 404: return "Not Found";
    case 405: return "Method Not Allowed"; case 408: return "Request Timeout"; case 409: return "Conflict";
    case 413: return "Content Too Large"; case 414: return "URI Too Long"; case 415: return "Unsupported Media Type";
    case 417: return "Expectation Failed"; case 431: return "Request Header Fields Too Large";
    case 503: return "Service Unavailable"; default: return "Internal Server Error";
  }
}

void BoundedWebServer::respond(Session &s, int code, const char *type, const char *body, size_t length) {
  if (s.responding) return;
  if (s.invalidHeaders || !safeHeader("Content-Type", type)) { close(s); return; }
  if (!s.responseHeader.reserve(RESPONSE_HEADER_LIMIT)) { close(s); return; }
  char start[160];
  std::snprintf(start, sizeof(start), "HTTP/1.1 %d %s\r\nContent-Length: %u\r\nConnection: close\r\n", code, reason(code), static_cast<unsigned>(length));
  s.responseHeader = start;
  s.responseHeader += "Content-Type: "; s.responseHeader += type; s.responseHeader += "\r\n";
  bool cache = false, nosniff = false;
  for (size_t i = 0; i < s.headerCount; ++i) {
    cache |= aqua::HttpRequest::equal(s.headers[i].name.c_str(), "Cache-Control");
    nosniff |= aqua::HttpRequest::equal(s.headers[i].name.c_str(), "X-Content-Type-Options");
    s.responseHeader += s.headers[i].name; s.responseHeader += ": ";
    s.responseHeader += s.headers[i].value; s.responseHeader += "\r\n";
  }
  if (!cache) s.responseHeader += "Cache-Control: no-store\r\n";
  if (!nosniff) s.responseHeader += "X-Content-Type-Options: nosniff\r\n";
  s.responseHeader += "\r\n";
  if (s.responseHeader.length() > RESPONSE_HEADER_LIMIT ||
      s.responseHeader.length() < 4 ||
      std::memcmp(s.responseHeader.c_str() + s.responseHeader.length() - 4, "\r\n\r\n", 4) != 0) { close(s); return; }
  s.body = body; s.bodyLength = length; s.headerSent = s.bodySent = 0;
  s.responding = true; s.started = s.progress = millis();
}

void BoundedWebServer::error(Session &s, int code) {
  if (s.request && (current_ < 0 || &sessions_[current_] != &s)) { s.request->reset(); s.request.reset(); }
  s.ownedBody = "{\"ok\":false,\"error\":\""; s.ownedBody += reason(code); s.ownedBody += "\"}";
  respond(s, code, "application/json; charset=utf-8", s.ownedBody.c_str(), s.ownedBody.length());
}
void BoundedWebServer::send(int code, const char *type, const String &body) {
  if (current_ < 0) return;
  auto &s = sessions_[current_];
  if (!s.active || s.responding) return;
  if (body.length() > DYNAMIC_BODY_LIMIT) { error(s, 500); return; }
  s.ownedBody = body;
  if (s.ownedBody.length() != body.length()) { error(s, 500); return; }
  respond(s, code, type, s.ownedBody.c_str(), s.ownedBody.length());
}
void BoundedWebServer::send_P(int code, const char *type, const char *body) { send_P(code, type, body, std::strlen(body)); }
void BoundedWebServer::send_P(int code, const char *type, const char *body, size_t length) {
  if (current_ >= 0 && sessions_[current_].active) respond(sessions_[current_], code, type, body, length);
}
void BoundedWebServer::sendHeader(const char *name, const char *value) {
  if (current_ < 0) return;
  auto &s = sessions_[current_];
  if (!s.active || s.responding) return;
  if (!safeHeader(name, value) || aqua::HttpRequest::equal(name, "Content-Length") || aqua::HttpRequest::equal(name, "Transfer-Encoding") || aqua::HttpRequest::equal(name, "Connection")) {
    s.invalidHeaders = true; return;
  }
  for (size_t i = 0; i < s.headerCount; ++i) if (aqua::HttpRequest::equal(s.headers[i].name.c_str(), name)) { s.headers[i].value = value; return; }
  if (s.headerCount == 8) { s.invalidHeaders = true; return; }
  s.headers[s.headerCount].name = name; s.headers[s.headerCount++].value = value;
}
const aqua::HttpRequest &BoundedWebServer::request() const {
  assert(current_ >= 0);
  assert(sessions_[current_].request);
  return *sessions_[current_].request;
}
String BoundedWebServer::uri() const { return String(request().path()); }
HTTPMethod BoundedWebServer::method() const { return request().method() == aqua::HttpRequest::Method::Post ? HTTP_POST : HTTP_GET; }
String BoundedWebServer::header(const char *name) const { return String(request().header(name)); }
String BoundedWebServer::arg(const char *name) const { return String(request().arg(name)); }
String BoundedWebServer::arg(int index) const { return index < 0 ? String() : String(request().arg(static_cast<size_t>(index))); }
String BoundedWebServer::argName(int index) const { return index < 0 ? String() : String(request().argName(static_cast<size_t>(index))); }
int BoundedWebServer::args() const { return static_cast<int>(request().args()); }
bool BoundedWebServer::hasArg(const char *name) const { return request().hasArg(name); }
