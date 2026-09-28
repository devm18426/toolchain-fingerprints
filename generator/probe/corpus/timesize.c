/* Compiles only if sizeof(time_t) == TSZ; the probe tries 4 and 8. */
#include <time.h>
typedef char check[sizeof(time_t) == TSZ ? 1 : -1];
int main(void) { return 0; }
