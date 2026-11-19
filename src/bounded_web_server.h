#pragma once

#include <Arduino.h>
#include <NetworkClient.h>
#include <NetworkServer.h>
#include <functional>
#include <memory>

#include "http_request.h"

enum HTTPMethod { HTTP_GET, HTTP_POST };

// Main-loop HTTP adapter. No task accesses controller state and no socket call
// waits for a slow peer. Client count, work per poll and connection age are bounded.
class BoundedWebServer {
 public:
  using Handler = std::function<void()>;
  static constexpr size_t CLIENT_LIMIT = 4;
  static constexpr size_t POLL_BYTE_BUDGET = 4096;
  static constexpr uint32_t REQUEST_TOTAL_MS = 5000, REQUEST_IDLE_MS = 1500;
  static constexpr uint32_t RESPONSE_TOTAL_MS = 15000, RESPONSE_IDLE_MS = 5000;

  explicit BoundedWebServer(uint16_t port = 80);
  ~BoundedWebServer();
  void begin();
  explicit operator bool();
  void handleClient();
  void on(const char *path, HTTPMethod method, Handler handler);
  void onNotFound(Handler handler);
  void collectHeaders(const char **, size_t) {}
  void send(int code, const char *type = "text/plain", const String &body = String());
  void send_P(int code, const char *type, const char *body);
  void send_P(int code, const char *type, const char *body, size_t length);
  void sendHeader(const char *name, const char *value);
  // Request access is valid only for the duration of the active route callback.
  const aqua::HttpRequest &request() const;
  String uri() const;
  HTTPMethod method() const;
  String header(const char *name) const;
  String arg(const char *name) const;
  String arg(int index) const;
  String argName(int index) const;
  int args() const;
  bool hasArg(const char *name) const;

 private:
  static constexpr size_t ROUTE_LIMIT = 32, RESPONSE_HEADER_LIMIT = 768, DYNAMIC_BODY_LIMIT = 8192;
  struct Header { String name; String value; };
  struct Session {
    NetworkClient client;
    // Allocated only while receiving/dispatching; outgoing pages retain no
    // 7KB input buffer, leaving room for TLS and the sensor/control tasks.
    std::unique_ptr<aqua::HttpRequest> request;
    bool active = false, responding = false, invalidHeaders = false;
    uint32_t started = 0, progress = 0;
    Header headers[8];
    size_t headerCount = 0;
    String responseHeader, ownedBody;
    const char *body = nullptr;
    size_t bodyLength = 0, headerSent = 0, bodySent = 0;
  };
  struct Route { String path; HTTPMethod method = HTTP_GET; Handler handler; };
  NetworkServer server_;
  Session sessions_[CLIENT_LIMIT];
  Route routes_[ROUTE_LIMIT];
  size_t routeCount_ = 0, first_ = 0;
  int current_ = -1;
  bool configured_ = true;
  Handler notFound_;
  void close(Session &session);
  void receive(Session &session, size_t &budget);
  void transmit(Session &session, size_t &budget);
  void dispatch(size_t index);
  void respond(Session &session, int code, const char *type, const char *body, size_t length);
  void error(Session &session, int code);
  static const char *reason(int code);
};
