// C++ exceptions: pulls in libstdc++ and the unwinder (libgcc_s).
#include <stdexcept>
int main() {
  try { throw std::runtime_error("x"); } catch (const std::exception &) { return 0; }
  return 1;
}
