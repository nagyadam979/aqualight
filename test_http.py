#!/usr/bin/env python3
"""Exercise the production bounded HTTP parser and server using socket doubles.

The doubles model backpressure, fragmentation, clocks and connection ownership;
no live hardware or network requests are performed.
"""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
CHECKS = r'''
#include <cassert>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <new>
#include <sanitizer/asan_interface.h>
#include <string>
#include <vector>
#include "bounded_web_server.h"
#include <lwip/sockets.h>

std::vector<void *> requestAllocations;
bool failNextRequestAllocation = false;
void *operator new(std::size_t size, const std::nothrow_t &) noexcept {
  if (size == sizeof(aqua::HttpRequest) && failNextRequestAllocation) { failNextRequestAllocation = false; return nullptr; }
  try {
    void *memory = ::operator new(size);
    if (size == sizeof(aqua::HttpRequest)) requestAllocations.push_back(memory);
    return memory;
  } catch (...) { return nullptr; }
}

uint32_t now = 0;
uint32_t millis() { return now; }
struct Socket {
  std::string incoming, outgoing;
  size_t inputOffset = 0, readMax = 512, writeMax = 100000;
  bool blocked = false, readEINTR = false, writeEINTR = false, closed = false;
};
std::map<int, Socket> sockets;
std::deque<int> pending;
int nextFd = 100;
size_t socketCalls = 0, movedBytes = 0;
void fakeClose(int fd) { sockets.at(fd).closed = true; }
NetworkClient fakeAccept() {
  if (pending.empty()) return {};
  int fd = pending.front(); pending.pop_front(); return NetworkClient(fd);
}
extern "C" ssize_t recv(int fd, void *data, size_t length, int flags) {
  ++socketCalls;
  assert(flags == MSG_DONTWAIT);
  auto &s = sockets.at(fd);
  if (s.closed) { errno = ECONNRESET; return -1; }
  if (s.readEINTR) { s.readEINTR = false; errno = EINTR; return -1; }
  size_t n = std::min(length, std::min(s.readMax, s.incoming.size() - s.inputOffset));
  if (!n) { errno = EAGAIN; return -1; }
  std::memcpy(data, s.incoming.data() + s.inputOffset, n);
  s.inputOffset += n; movedBytes += n;
  return static_cast<ssize_t>(n);
}
extern "C" ssize_t send(int fd, const void *data, size_t length, int flags) {
  ++socketCalls;
  assert(flags == MSG_DONTWAIT);
  auto &s = sockets.at(fd);
  if (s.closed) { errno = EPIPE; return -1; }
  if (s.writeEINTR) { s.writeEINTR = false; errno = EINTR; return -1; }
  if (s.blocked) { errno = EAGAIN; return -1; }
  size_t n = std::min(length, s.writeMax);
  s.outgoing.append(static_cast<const char *>(data), n); movedBytes += n;
  return static_cast<ssize_t>(n);
}
int connectRequest(std::string request, size_t readMax = 512) {
  int fd = nextFd++; sockets[fd].incoming = request; sockets[fd].readMax = readMax;
  pending.push_back(fd); return fd;
}
std::string get(const std::string &path) { return "GET " + path + " HTTP/1.1\r\nHost: local\r\n\r\n"; }
std::string post(const std::string &path, const std::string &body, const std::string &extra = "") {
  return "POST " + path + " HTTP/1.1\r\nHost: local\r\nContent-Type: application/x-www-form-urlencoded\r\nX-Aqua-Request: 1\r\nContent-Length: " +
    std::to_string(body.size()) + "\r\n" + extra + "\r\n" + body;
}
void poll(BoundedWebServer &server) {
  socketCalls = movedBytes = 0;
  server.handleClient();
  assert(movedBytes <= BoundedWebServer::POLL_BYTE_BUDGET);
  assert(socketCalls <= 12); // Four recv + up to two sends per session.
}
void finish(BoundedWebServer &server, int fd, unsigned maxPolls = 1000) {
  for (unsigned i = 0; i < maxPolls && !sockets.at(fd).closed; ++i) { poll(server); ++now; }
  assert(sockets.at(fd).closed);
}
void parseOk(const std::string &wire, aqua::HttpRequest &request) {
  request.reset();
  for (unsigned char byte : wire) assert(request.feed(&byte, 1) == 1);
  assert(request.complete() && !request.errorStatus());
}
void parseError(const std::string &wire, int code) {
  aqua::HttpRequest request;
  request.feed(reinterpret_cast<const uint8_t *>(wire.data()), wire.size());
  assert(!request.complete() && request.errorStatus() == code);
}

int main() {
  static_assert(sizeof(BoundedWebServer) < 10000, "Idle/outgoing clients must not retain inline 7KB request parsers");
  aqua::HttpRequest request;
  parseOk(get("/api/peer/command?id=a%2Bb&empty=&flag&space=hello+world"), request);
  assert(std::strcmp(request.path(), "/api/peer/command") == 0);
  assert(request.method() == aqua::HttpRequest::Method::Get && request.args() == 4);
  assert(std::strcmp(request.arg("id"), "a+b") == 0);
  assert(std::strcmp(request.arg(0), "a+b") == 0 && std::strcmp(request.arg(-1), "") == 0);
  assert(std::strcmp(request.arg("space"), "hello world") == 0);
  assert(request.hasArg("empty") && request.hasArg("flag") && !request.hasArg("missing"));
  parseOk(post("/api/manual?extra=one", "warm=12&cool=23&warm=24"), request);
  assert(request.args() == 4 && std::strcmp(request.argName(0), "extra") == 0);
  assert(std::strcmp(request.arg("warm"), "12") == 0); // Duplicates remain countable.
  parseOk(post("/api/wifi", "ssid=p%C3%A1ra&password=a%26b%3Dc%2B123"), request);
  assert(std::strcmp(request.arg("ssid"), "p\xc3\xa1ra") == 0);
  assert(std::strcmp(request.arg("password"), "a&b=c+123") == 0);
  parseError(get("/api/state?bad=%00"), 400);
  parseError(get("/api/state?bad=%0"), 400);
  parseError(get("/api/state?bad=%gg"), 400);
  parseError(post("/api/mode", "mode=auto", "Content-Length: 9\r\n"), 400);
  parseError(post("/api/mode", "mode=auto", "Transfer-Encoding: chunked\r\n"), 400);
  parseError(post("/api/mode", "mode=auto", "Content-Type:\r\n"), 400);
  parseError("POST / HTTP/1.1\r\nHost: local\r\nX-Aqua-Request:\r\nX-Aqua-Request: 1\r\nContent-Length: 0\r\n\r\n", 400);
  parseError("POST / HTTP/1.1\r\nHost: local\r\nContent-Length: 99999999999999999999999\r\n\r\n", 413);
  parseError("POST / HTTP/1.1\r\nHost: local\r\nContent-Length: +1\r\n\r\nx", 400);
  parseError("GET / HTTP/1.1\nHost: local\n\n", 400);
  parseError("GET / HTTP/1.1\r\nHost: local\r\n bad: yes\r\n\r\n", 400);
  parseError("GET / HTTP/1.1\r\n\r\n", 400);
  parseError("DELETE / HTTP/1.1\r\nHost: local\r\n\r\n", 405);
  parseError(get("/" + std::string(161, 'a')), 414);
  parseError(post("/", "a=1&b=2&c=3&d=4&e=5&f=6&g=7&h=8&i=9"), 413);
  parseError(std::string("GET / HTTP/1.1\r\nHost: local\r\nLong: ") + std::string(4096, 'x') + "\r\n\r\n", 431);
  parseOk("GET / HTTP/1.0\r\n\r\n", request);
  parseOk(post("/", "a=" + std::string(aqua::HttpRequest::BODY_LIMIT - 2, 'x')), request);
  assert(request.args() == 1 && std::strlen(request.arg("a")) == aqua::HttpRequest::BODY_LIMIT - 2);
  parseError(post("/", "a=" + std::string(aqua::HttpRequest::BODY_LIMIT - 1, 'x')), 413);

  BoundedWebServer server;
  unsigned mutations = 0, reads = 0;
  std::string page(70000, 'P');
  server.on("/api/state", HTTP_GET, [&] {
    ++reads;
    assert(server.uri() == "/api/state" && server.method() == HTTP_GET);
    server.send(200, "application/json", String("{\"sensorOk\":true}"));
  });
  server.on("/api/mode", HTTP_POST, [&] {
    if (!(server.header("X-Aqua-Request") == "1") || server.args() != 1 || !(server.arg("mode") == "auto")) {
      server.send(400, "application/json", String("{\"ok\":false}")); return;
    }
    ++mutations;
    assert(server.argName(0) == "mode" && server.arg(0) == "auto");
    server.send(200, "application/json", String("{\"ok\":true}"));
  });
  server.on("/control", HTTP_GET, [&] {
    server.sendHeader("Content-Encoding", "gzip");
    server.send_P(200, "text/html", page.data(), page.size());
  });
  const char binary[] = {'G', 0, 'Z', 1, 2};
  server.on("/binary", HTTP_GET, [&] {
    server.sendHeader("Content-Encoding", "gzip");
    server.send_P(200, "text/html", binary, sizeof(binary));
  });
  server.on("/stream", HTTP_GET, [&] {
    server.sendHeader("Content-Disposition", "attachment; filename=history.csv");
    server.sendStream(200, "text/csv", [step = 0](char *bytes, size_t size) mutable -> int {
      assert(size >= 3);
      switch (step++) {
        case 0: std::memcpy(bytes, "abc", 3); return 3;
        case 1: return -2;
        case 2: std::memcpy(bytes, "\r\n", 2); return 2;
        default: return 0;
      }
    });
  });
  server.on("/stream-error", HTTP_GET, [&] {
    server.sendStream(200, "text/csv", [](char *, size_t) { return -1; });
  });
  server.on("/bad-header", HTTP_GET, [&] {
    const auto &request = server.request();
    server.sendHeader("Bad", "value\r\ninjection");
    server.send(200, "text/plain", String("ignored"));
    // Even an aborted response must not invalidate the callback's request.
    assert(std::strcmp(request.path(), "/bad-header") == 0);
  });
  server.onNotFound([&] { server.send(404, "application/json", String("{}")); });
  server.begin(); assert(static_cast<bool>(server));

  // An incomplete idle client and an unread large response must never starve
  // a healthy status request. Fragmentation must not trigger a mutation early.
  int idle = connectRequest(""); poll(server);
  assert(requestAllocations.empty()); // Empty speculative connections reserve no parser heap.
  int unread = connectRequest(get("/control")); sockets[unread].blocked = true; poll(server);
  assert(requestAllocations.size() == 1 && __asan_address_is_poisoned(requestAllocations.back()));
  assert(!sockets[unread].closed); // Outgoing page has already released its 7KB parser.
  int healthy = connectRequest(get("/api/state"), 3); finish(server, healthy);
  assert(reads == 1 && !sockets[idle].closed && !sockets[unread].closed);
  assert(sockets[healthy].outgoing.find("200 OK") != std::string::npos);
  assert(sockets[healthy].outgoing.find("{\"sensorOk\":true}") != std::string::npos);
  assert(sockets[healthy].outgoing.find("Content-Encoding") == std::string::npos); // Per-session headers.
  failNextRequestAllocation = true;
  int noMemory = connectRequest(get("/api/state")); finish(server, noMemory);
  assert(reads == 1 && sockets[noMemory].outgoing.empty()); // No handler mutation on allocation failure.

  std::string mutation = post("/api/mode", "mode=auto");
  int partial = connectRequest(mutation.substr(0, mutation.size() - 1));
  for (unsigned i = 0; i < 10; ++i) { poll(server); ++now; }
  assert(mutations == 0 && !sockets[partial].closed);
  assert(!__asan_address_is_poisoned(requestAllocations.back())); // Incomplete input still owns its parser.
  sockets[partial].incoming += mutation.back(); finish(server, partial);
  assert(__asan_address_is_poisoned(requestAllocations.back()));
  assert(mutations == 1);
  int duplicate = connectRequest(post("/api/mode", "mode=auto&mode=off")); finish(server, duplicate);
  assert(mutations == 1);
  int ambiguous = connectRequest(post("/api/mode", "mode=auto", "Transfer-Encoding: chunked\r\n")); finish(server, ambiguous);
  assert(mutations == 1 && sockets[ambiguous].outgoing.find("400 Bad Request") != std::string::npos);

  // Response progress can be short or interrupted; bytes must remain exact,
  // including the temporary String response after the handler has returned.
  int shortWrite = connectRequest(get("/api/state"));
  sockets[shortWrite].writeMax = 7; sockets[shortWrite].readEINTR = true; sockets[shortWrite].writeEINTR = true;
  finish(server, shortWrite);
  assert(sockets[shortWrite].outgoing.find("Content-Length: 17\r\n") != std::string::npos);
  assert(sockets[shortWrite].outgoing.substr(sockets[shortWrite].outgoing.find("\r\n\r\n") + 4) == "{\"sensorOk\":true}");

  // Request idle timeout starts an error response; a response blocked by the
  // peer is ultimately discarded. Both clocks are rollover safe.
  now += BoundedWebServer::REQUEST_IDLE_MS; poll(server);
  assert(sockets[idle].closed && sockets[idle].outgoing.find("408 Request Timeout") != std::string::npos);
  now += BoundedWebServer::RESPONSE_IDLE_MS; poll(server);
  assert(sockets[unread].closed);

  int compressed = connectRequest(get("/binary")); finish(server, compressed);
  const auto &compressedResponse = sockets[compressed].outgoing;
  assert(compressedResponse.find("Content-Encoding: gzip\r\n") != std::string::npos);
  assert(compressedResponse.substr(compressedResponse.find("\r\n\r\n") + 4) == std::string(binary, sizeof(binary)));
  int streamed = connectRequest(get("/stream"));
  sockets[streamed].writeMax = 2; sockets[streamed].writeEINTR = true;
  finish(server, streamed);
  const auto &streamedResponse = sockets[streamed].outgoing;
  assert(streamedResponse.find("Transfer-Encoding: chunked\r\n") != std::string::npos);
  assert(streamedResponse.find("Content-Length:") == std::string::npos);
  assert(streamedResponse.find("Content-Disposition: attachment; filename=history.csv\r\n") != std::string::npos);
  assert(streamedResponse.substr(streamedResponse.find("\r\n\r\n") + 4) == "3\r\nabc\r\n2\r\n\r\n\r\n0\r\n\r\n");
  int legacy = connectRequest("GET /stream HTTP/1.0\r\n\r\n");
  finish(server, legacy);
  const auto &legacyResponse = sockets[legacy].outgoing;
  assert(legacyResponse.find("HTTP/1.0 200 OK\r\n") == 0);
  assert(legacyResponse.find("Transfer-Encoding:") == std::string::npos);
  assert(legacyResponse.substr(legacyResponse.find("\r\n\r\n") + 4) == "abc\r\n");
  int failedStream = connectRequest(get("/stream-error")); finish(server, failedStream);
  const auto &failedResponse = sockets[failedStream].outgoing;
  assert(failedResponse.find("\r\n\r\n") + 4 == failedResponse.size()); // No terminating chunk on errors.
  int reused = connectRequest(get("/api/state")); finish(server, reused);
  assert(sockets[reused].outgoing.find("Content-Encoding") == std::string::npos);
  int invalidHeader = connectRequest(get("/bad-header")); finish(server, invalidHeader);
  assert(sockets[invalidHeader].outgoing.empty());

  now = UINT32_MAX - 1000;
  int wrap = connectRequest(""); poll(server);
  now = 498; poll(server); assert(!sockets[wrap].closed);
  now = 499; poll(server); assert(sockets[wrap].closed);

  // A client trickling a byte just before each idle deadline still loses its
  // slot at the absolute request deadline, so no indefinite reservation exists.
  now = 10000;
  int trickle = connectRequest("G"); poll(server);
  for (unsigned i = 1; i < 5; ++i) { now = 10000 + i * 1000; sockets[trickle].incoming += 'E'; poll(server); }
  now = 15000; poll(server);
  assert(sockets[trickle].closed && sockets[trickle].outgoing.find("408") != std::string::npos);

  // Likewise, a slowly progressing response is bounded by total age even if
  // it avoids the idle deadline. The server releases the static body reference.
  now = 20000;
  int slow = connectRequest(get("/control")); sockets[slow].writeMax = 1; poll(server);
  for (unsigned i = 1; i < 15; ++i) { now = 20000 + i * 1000; poll(server); assert(!sockets[slow].closed); }
  now = 35000; poll(server); assert(sockets[slow].closed);

  std::puts("PASS HTTP: production parsing/framing, query/forms/duplicates, delayed mutation, concurrent slow clients, short writes/EAGAIN/EINTR, bounded work and idle/absolute deadlines with rollover");
}
'''

with tempfile.TemporaryDirectory(prefix="aqualight-http-") as directory:
    temp = Path(directory)
    cpp, binary = temp / "check.cpp", temp / "check"
    cpp.write_text(CHECKS)
    subprocess.run([
        os.environ.get("CXX", "c++"), "-std=c++17", "-Wall", "-Wextra", "-Werror",
        "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
        "-I", str(ROOT / "test_http_support"), "-I", str(ROOT / "src"),
        str(cpp), str(ROOT / "src/bounded_web_server.cpp"), "-o", str(binary),
    ], check=True)
    subprocess.run([str(binary)], check=True)
