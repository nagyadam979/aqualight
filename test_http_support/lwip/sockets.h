#pragma once
#include <cstddef>
#include <sys/types.h>
constexpr int MSG_DONTWAIT = 0x40;
extern "C" ssize_t recv(int fd, void *data, size_t length, int flags);
extern "C" ssize_t send(int fd, const void *data, size_t length, int flags);
