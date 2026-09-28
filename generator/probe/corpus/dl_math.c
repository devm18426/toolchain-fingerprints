/* dlopen, libm, and 64-bit division (libgcc helpers on 32-bit targets). */
#include <dlfcn.h>
#include <math.h>
volatile double x = 2.0;
volatile unsigned long long a = 1ULL << 40, b = 3;
int main(void) {
  void *h = dlopen("libz.so.1", RTLD_LAZY);
  if (h) dlclose(h);
  return (int)(sqrt(x) + pow(x, x)) + (int)(a / b % 7);
}
