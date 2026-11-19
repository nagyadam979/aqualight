#pragma once
#include <memory>
void fakeClose(int fd);
class NetworkClient {
  struct Handle {
    int fd;
    explicit Handle(int value) : fd(value) {}
    ~Handle() { if (fd >= 0) fakeClose(fd); }
  };
  std::shared_ptr<Handle> handle_;
 public:
  NetworkClient() = default;
  explicit NetworkClient(int fd) : handle_(std::make_shared<Handle>(fd)) {}
  int fd() const { return handle_ ? handle_->fd : -1; }
  void stop() { if (fd() >= 0) { fakeClose(fd()); handle_->fd = -1; } handle_.reset(); }
  int setNoDelay(bool) { return 0; }
};
