/* pthreads + the time-related calls that a 64-bit time_t routes through the
   *_time64 syscalls on 32-bit targets (select, clock_gettime). */
#include <pthread.h>
#include <sys/select.h>
#include <time.h>
static void *run(void *p) { return p; }
int main(void) {
  pthread_t t; struct timeval tv = {0, 0}; struct timespec ts;
  pthread_create(&t, 0, run, 0); pthread_join(t, 0);
  clock_gettime(CLOCK_MONOTONIC, &ts);
  nanosleep(&ts, 0);
  return select(0, 0, 0, 0, &tv) + (int)time(0);
}
